"""
Phase 4, step 3: predict the five keypoints from a crop.

evaluate.py drives everything through one small interface:

    class Something:
        def fit(self, images, targets):   # uint8 (N,384,384,3), targets (N,5,2) in [0,1]
        def predict(self, images):        # -> (N,5,2) in [0,1]

`MeanKeypoints` is the floor -- it predicts the training-set mean position for every
image, which is not trivial here because every crop is centred on the pitcher by
construction. It scores 30.7 deg end to end. Anything that cannot beat it clearly has
learned nothing from the pixels.

What the problem actually is
----------------------------
Off-the-shelf COCO pose on these exact frames: shoulder 10.7 px, off_shoulder 4.8,
elbow 7.0, wrist 15.2 -- and 1 px is worth ~0.54 deg at the median 105 px
shoulder->ball baseline. Those body points with a hand-clicked ball score RMSE 3.188
against 2.651 fully hand-clicked, so the whole body half of the problem is worth about
half a degree. Nothing off the shelf finds the ball at all. The ball is the job.

Why soft-argmax rather than a regression head
---------------------------------------------
Both are implemented and were measured head to head on one held-out fold, 40 epochs,
median pixel error:

    keypoint        mean baseline    fc head    soft-argmax    soft-argmax 28x28
    shoulder                15.9        8.4           10.8                 10.1
    off_shoulder            64.0       10.9            8.9                  8.6
    elbow                   64.4        9.0           13.1                 11.9
    wrist                  120.0       13.6            9.9                  9.5
    ball                   146.1       13.6            8.0                  5.9

The stock resnet head runs global average pooling over the final feature map, which
averages away exactly the spatial information a coordinate is made of, then asks a
linear layer to reconstruct position from what survived. It works, but it is a poor use
of 144 training images. Soft-argmax predicts a heatmap per keypoint and takes its
expectation, so the output is a coordinate by construction and no heatmap targets are
needed. It more than halves the error on the ball, which is the point of the exercise,
and trains faster (388 s vs 500 s for 40 epochs) because it stops at layer3.

The 14x14 version lost to the fc head on shoulder and elbow; upsampling the head to a
28x28 grid fixed that and improved every point. That is `upsample=2`, the default.

Both heads are kept so the comparison stays reproducible: `head="fc"` re-runs it.
"""

import math

import numpy as np


class MeanKeypoints:
    """
    Predicts the training-set mean keypoint position for every image.

    Not a real model -- it is the floor. Because every crop is centred on the pitcher by
    construction, this is a genuinely non-trivial baseline, and any model that cannot
    clearly beat it has learned nothing from the pixels.
    """

    def __init__(self, **_):
        self.mean = None

    def fit(self, images, targets):
        self.mean = targets.mean(axis=0)
        return self

    def predict(self, images):
        return np.repeat(self.mean[None], len(images), axis=0)


def SoftArgmaxHead(resnet, gridPad=0.125, upsample=2):
    """
    resnet18 truncated at layer3, a small conv head, and a differentiable arg-max.

    Why not the stock resnet head: `avgpool` collapses the final 7x7 feature map to a
    single vector, averaging away exactly the spatial information a coordinate is made
    of, and then asks a linear layer to reconstruct position from what survived. It can
    be made to work, but it is a poor use of 144 training images.

    Soft-argmax instead predicts one heatmap per keypoint, softmaxes it over the image,
    and takes the expected (x, y) under that distribution. The output is a coordinate by
    construction, it is fully differentiable so no heatmap targets are needed, and it is
    far more parameter-efficient -- which is what matters at this sample size.

    Layer3 rather than layer4: at 224 input that is a 14x14 grid instead of 7x7. The
    expectation interpolates between cells so precision is not limited to the grid, but
    starting twice as fine costs little and helps.

    The grid deliberately spans [-gridPad, 1+gridPad] rather than [0, 1]. An expectation
    over a bounded grid can never reach the extremes, and one of the 180 targets
    legitimately sits at 1.088, outside the crop entirely. Padding the grid keeps
    edge points reachable instead of merely improbable.
    """
    import torch

    class Module(torch.nn.Module):
        def __init__(self):
            super().__init__()
            self.stem = torch.nn.Sequential(
                resnet.conv1, resnet.bn1, resnet.relu, resnet.maxpool,
                resnet.layer1, resnet.layer2, resnet.layer3)
            layers = [torch.nn.Conv2d(256, 128, 3, padding=1),
                      torch.nn.BatchNorm2d(128),
                      torch.nn.ReLU(inplace=True)]
            if upsample > 1:
                # A 14x14 grid at 224 input is 16 px per cell. The expectation
                # interpolates between cells, but the heatmap the softmax is shaped over
                # is still coarse, and it showed: on a held-out fold the 14x14 head lost
                # to the fc head on shoulder and elbow while beating it soundly on wrist
                # and ball. Upsampling before the final 1x1 gives the softmax somewhere
                # finer to put its mass, for very little compute at 128 channels.
                layers += [torch.nn.Upsample(scale_factor=upsample, mode="bilinear",
                                             align_corners=False),
                           torch.nn.Conv2d(128, 128, 3, padding=1),
                           torch.nn.BatchNorm2d(128),
                           torch.nn.ReLU(inplace=True)]
            layers.append(torch.nn.Conv2d(128, 5, 1))
            self.head = torch.nn.Sequential(*layers)
            # Zero the final conv: a uniform heatmap has its expectation at the centre
            # of the crop, which is where the pitcher is by construction. Training
            # therefore starts from a sensible guess rather than an arbitrary one.
            torch.nn.init.zeros_(self.head[-1].weight)
            torch.nn.init.zeros_(self.head[-1].bias)
            self.gridPad = gridPad

        def headParameters(self):
            return self.head.parameters()

        def forward(self, x):
            maps = self.head(self.stem(x))
            batch, points, height, width = maps.shape
            weights = torch.softmax(maps.view(batch, points, -1), dim=2)
            weights = weights.view(batch, points, height, width)

            span = 1.0 + 2 * self.gridPad
            xs = (torch.arange(width, dtype=torch.float32, device=x.device) + 0.5) / width
            ys = (torch.arange(height, dtype=torch.float32, device=x.device) + 0.5) / height
            xs = xs * span - self.gridPad
            ys = ys * span - self.gridPad

            predictedX = (weights.sum(dim=2) * xs).sum(dim=2)
            predictedY = (weights.sum(dim=3) * ys).sum(dim=2)
            return torch.stack([predictedX, predictedY], dim=2)

    return Module()


class ResnetKeypoints:
    """
    resnet18 backbone, ImageNet weights, regressing 5 (x, y) pairs in crop coordinates.

    Direct coordinate regression rather than heatmaps. Heatmaps are usually more
    accurate, but they spend their accuracy budget on data this dataset does not have:
    at n=180 a heatmap head has far more to learn and nothing to learn it from. Worth
    revisiting if the label count ever doubles.

    Choices that are load-bearing rather than defaults:

    - **Huber loss, not MSE.** With 180 hand-clicked examples, one mis-clicked ball
      would otherwise dominate the gradient. `HUBER_BETA` is set in normalized units
      where 1.0 is the crop width (~380 px), so 0.01 is about 4 px -- errors smaller
      than a click's own precision stay quadratic, larger ones stop pulling so hard.

    - **Two learning rates.** The backbone arrives already knowing what a person looks
      like and only needs nudging; the head is random and needs to move. One shared
      rate either destroys the pretrained features or leaves the head untrained.

    - **No sigmoid on the output.** It would bound predictions to the crop, which is
      almost right -- but 1 of 180 targets legitimately sits at 1.088, just outside,
      and a sigmoid makes that point unreachable rather than merely unlikely.

    - **Ball weighted higher in the loss.** The angle is measured shoulder->ball, so
      those two points are what the Ridge actually consumes; off_shoulder and elbow
      only enter as torso-tilt corrections. Weighting matches the loss to the metric.
    """

    HUBER_BETA = 0.01
    KEYPOINT_WEIGHTS = [1.5, 0.5, 0.5, 1.0, 2.0]   # shoulder, off_shoulder, elbow, wrist, ball
    GRID_PAD = 0.125    # soft-argmax grid extends past the crop -- see SoftArgmaxHead

    def __init__(self, size=224, epochs=100, lr=1e-3, backboneLr=3e-4,
                 batchSize=16, weightDecay=1e-4, seed=0, verbose=False,
                 head="softargmax", warmupEpochs=5, upsample=2):
        self.upsample = upsample
        self.head = head
        self.warmupEpochs = warmupEpochs
        self.size = size
        self.epochs = epochs
        self.lr = lr
        self.backboneLr = backboneLr
        self.batchSize = batchSize
        self.weightDecay = weightDecay
        self.seed = seed
        self.verbose = verbose
        self.model = None

    def _build(self):
        import torch
        import torchvision

        torch.manual_seed(self.seed)
        resnet = torchvision.models.resnet18(
            weights=torchvision.models.ResNet18_Weights.IMAGENET1K_V1)

        if self.head == "fc":
            resnet.fc = torch.nn.Linear(resnet.fc.in_features, 10)
            # Zero weights, not small random ones. With 512 inputs even std=0.01 puts
            # about 0.16 of noise on the output -- 60 px -- so training would start a
            # long way WORSE than predicting the mean and spend its first epochs
            # climbing back. Zero weights plus a mean-valued bias start it exactly at
            # the mean baseline; the weight gradient is still non-zero, so it learns
            # from step one.
            torch.nn.init.zeros_(resnet.fc.weight)
            torch.nn.init.zeros_(resnet.fc.bias)
            return resnet

        return SoftArgmaxHead(resnet, gridPad=self.GRID_PAD,
                              upsample=self.upsample)

    def fit(self, images, targets):
        import torch
        from phase4 import dataset

        torch.set_num_threads(8)
        rng = np.random.default_rng(self.seed)
        self.model = self._build()
        images = dataset.resizeAll(images, self.size)

        if self.head == "fc":
            # Bias the head to predict the training mean, so it starts at the mean
            # baseline rather than at noise. The soft-argmax head needs no equivalent:
            # a zero-initialised heatmap is uniform, whose expectation is the centre of
            # the crop -- which is where the pitcher is, by construction.
            mean = targets.mean(axis=0).reshape(-1)
            with torch.no_grad():
                self.model.fc.bias.copy_(torch.tensor(mean, dtype=torch.float32))

        head = list(self.model.headParameters() if self.head != "fc"
                    else self.model.fc.parameters())
        headIds = {id(p) for p in head}
        backbone = [p for p in self.model.parameters() if id(p) not in headIds]
        optimizer = torch.optim.AdamW(
            [{"params": backbone, "lr": self.backboneLr},
             {"params": head, "lr": self.lr}], weight_decay=self.weightDecay)
        # Linear warmup then cosine. Without the warmup the first optimiser step off a
        # zero-initialised head is large enough to push the model measurably worse than
        # the mean baseline, and several epochs go on undoing it.
        def lrScale(epoch):
            if epoch < self.warmupEpochs:
                return (epoch + 1) / self.warmupEpochs
            progress = (epoch - self.warmupEpochs) / max(1, self.epochs - self.warmupEpochs)
            return 0.5 * (1.0 + math.cos(math.pi * progress))

        schedule = torch.optim.lr_scheduler.LambdaLR(optimizer, lrScale)

        weights = torch.tensor(self.KEYPOINT_WEIGHTS, dtype=torch.float32).view(1, 5, 1)
        count = len(images)

        self.model.train()
        for epoch in range(self.epochs):
            order = rng.permutation(count)
            total = 0.0

            for start in range(0, count, self.batchSize):
                batchIdx = order[start:start + self.batchSize]
                batchImages = images[batchIdx]
                batchTargets = targets[batchIdx]

                # Jitter each crop independently: at inference the crop comes from a
                # detector box that wobbles, so perfectly centred training crops teach
                # a precision Stage A does not have.
                jittered = np.empty_like(batchImages)
                moved = np.empty_like(batchTargets)
                for position in range(len(batchIdx)):
                    jittered[position], moved[position] = dataset.jitterCrop(
                        batchImages[position], batchTargets[position], rng)

                # Identity keypoint mapping -- `shoulder` means the THROWING shoulder,
                # so a mirrored righty is a valid lefty with the same point identities.
                if rng.random() < 0.5:
                    jittered, moved = dataset.flip(jittered, moved)

                x = dataset.toTensorBatch(jittered)
                x = x * float(rng.uniform(0.85, 1.15))          # brightness jitter
                y = torch.from_numpy(moved)

                predicted = self.model(x).view(-1, 5, 2)
                perPoint = torch.nn.functional.smooth_l1_loss(
                    predicted, y, beta=self.HUBER_BETA, reduction="none")
                loss = (perPoint * weights).mean()

                optimizer.zero_grad()
                loss.backward()
                optimizer.step()
                total += float(loss) * len(batchIdx)

            schedule.step()
            if self.verbose and (epoch % 10 == 0 or epoch == self.epochs - 1):
                print(f"    epoch {epoch + 1:>3}/{self.epochs}  loss {total / count:.5f}",
                      flush=True)
        return self

    def predict(self, images):
        import torch
        from phase4 import dataset

        self.model.eval()
        images = dataset.resizeAll(images, self.size)
        out = []
        with torch.no_grad():
            for start in range(0, len(images), self.batchSize):
                x = dataset.toTensorBatch(images[start:start + self.batchSize])
                out.append(self.model(x).view(-1, 5, 2).numpy())
        return np.concatenate(out, axis=0).astype(np.float32)


MODELS = {
    "mean": MeanKeypoints,
    "resnet": ResnetKeypoints,
}
