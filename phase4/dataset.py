"""
Loading and augmentation for the crop dataset. Plumbing, kept out of the model file.

The one piece here that is easy to get wrong is the horizontal flip.

`shoulder` is the THROWING shoulder and `off_shoulder` the glove side -- defined by
p_throws, not by which side of the image they appear on. So mirroring a right-hander's
crop produces a perfectly valid left-hander-looking example whose keypoint *identities*
are unchanged: the throwing shoulder is still the throwing shoulder. The flip is
therefore x -> 1 - x with NO index permutation, which is not what you would do with a
COCO skeleton (where left/right wrists must swap).

That matters twice over: it doubles an n=180 dataset for free, and it rebalances the
126 R / 54 L split that would otherwise teach the model that arms point left.
"""

import os

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
NPZ_PATH = os.path.join(HERE, "crops.npz")
INDEX_PATH = os.path.join(HERE, "index.csv")

# ImageNet statistics -- the resnet backbone was pretrained with these.
IMAGENET_MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
IMAGENET_STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)


def load(npzPath=NPZ_PATH, indexPath=INDEX_PATH):
    """Returns (images uint8 NHWC, targets N x 5 x 2 in [0,1], transforms N x 3, index)."""
    data = np.load(npzPath, allow_pickle=True)
    index = pd.read_csv(indexPath)
    return data["images"], data["targets"], data["transforms"], index


def flip(images, targets):
    """Mirror horizontally. Keypoint identities are preserved -- see the module docstring."""
    return images[:, :, ::-1].copy(), np.stack(
        [1.0 - targets[..., 0], targets[..., 1]], axis=-1)


def resizeAll(images, size):
    """
    Downscale the stored 384 px crops to the training resolution, once.

    Crops are fixed, so resizing them inside the training loop redoes identical work
    every epoch -- measured at roughly half the per-epoch cost. Doing it here with
    INTER_AREA also keeps the quality of a proper area-average downscale, which the
    nearest-neighbour gather in jitterCrop would not give.
    """
    import cv2

    if images.shape[1] == size:
        return images
    out = np.empty((len(images), size, size, images.shape[3]), dtype=images.dtype)
    for position, image in enumerate(images):
        out[position] = cv2.resize(image, (size, size), interpolation=cv2.INTER_AREA)
    return out


def jitterCrop(image, target, rng, maxShift=0.06, maxScale=0.12):
    """
    Re-crop slightly off-centre and off-scale, adjusting the targets to match.

    This is the most important augmentation here, and not for the usual
    regularisation reasons: at inference the crop comes from a detector box that
    wobbles frame to frame. Training only on perfectly centred crops teaches the model
    to expect a precision Stage A does not have.
    """
    size = image.shape[0]
    scale = 1.0 + rng.uniform(-maxScale, maxScale)
    shiftX = rng.uniform(-maxShift, maxShift)
    shiftY = rng.uniform(-maxShift, maxShift)

    side = size * scale
    x0 = (size - side) / 2.0 + shiftX * size
    y0 = (size - side) / 2.0 + shiftY * size

    # Map targets into the new window: normalized coords rescale linearly.
    moved = (target * size - np.array([x0, y0], dtype=np.float32)) / side

    xs = np.clip((np.arange(size) / size * side + x0).astype(int), 0, size - 1)
    ys = np.clip((np.arange(size) / size * side + y0).astype(int), 0, size - 1)
    return image[np.ix_(ys, xs)], moved.astype(np.float32)


def toTensorBatch(images, size=None):
    """
    uint8 NHWC -> float32 NCHW, ImageNet-normalized, optionally resized.

    Resize happens here rather than in buildDataset.py so input size stays a training
    flag: crops are stored at 384 (~1:1 with the source pixels) and you can try 224 for
    fast iteration or 320 for accuracy without rebuilding anything.
    """
    import torch
    import torch.nn.functional as functional

    batch = torch.from_numpy(np.ascontiguousarray(images)).float().div_(255.0)
    batch = batch.permute(0, 3, 1, 2)
    if size and size != batch.shape[-1]:
        batch = functional.interpolate(batch, size=(size, size),
                                       mode="bilinear", align_corners=False)
    mean = torch.tensor(IMAGENET_MEAN).view(1, 3, 1, 1)
    std = torch.tensor(IMAGENET_STD).view(1, 3, 1, 1)
    return (batch - mean) / std


def toPixels(normalized, transforms):
    """
    Normalized crop coordinates -> full-frame pixels.

    normalized: N x 5 x 2 in [0,1]; transforms: N x 3 of (x0, y0, side).
    Verified to round-trip the hand labels to within 1e-4 px.
    """
    origin = transforms[:, None, :2]
    side = transforms[:, None, 2:3]
    return origin + normalized * side


def coordDict(pixels):
    """One row of N x 5 x 2 pixels -> the {name}_{x,y} dict features.py expects."""
    import features
    coords = {}
    for index, name in enumerate(features.KEYPOINT_NAMES):
        coords[f"{name}_x"] = float(pixels[index][0])
        coords[f"{name}_y"] = float(pixels[index][1])
    return coords
