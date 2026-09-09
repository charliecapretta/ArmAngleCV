"""
Minimal BaseballCV demo: download a pretrained YOLO model via LoadTools and run it
on a real MLB broadcast frame.

The model alias is `glove_tracking`, not `ball_tracking`. Per the repo README, the
`glove_tracking.pt` weights are the ones "trained to detect and track the catcher's
glove, the ball, homeplate, and the pitcher's rubber from a broadcast feed", which is
the four-class set we want here. `ball_tracking` only detects the baseball.

Run from the repo root so LoadTools caches the weights under models/od/YOLO/.
"""

import argparse
import os
import sys

from baseballcv.functions import LoadTools
from ultralytics import YOLO

MODEL_ALIAS = "glove_tracking"
DEFAULT_IMAGE = "sample_frame.jpg"
DEFAULT_OUTPUT = "sample_frame_annotated.jpg"
CONFIDENCE_THRESHOLD = 0.25


def downloadModel(modelAlias=MODEL_ALIAS):
    """Fetch the pretrained weights through BaseballCV's LoadTools and return the path."""
    loadTools = LoadTools()
    modelPath = loadTools.load_model(modelAlias)
    print(f"Model weights: {modelPath}")
    return modelPath


def runInference(modelPath, imagePath, outputPath, confidence=CONFIDENCE_THRESHOLD):
    """Run the YOLO model on one image, print detections, and save an annotated copy."""
    model = YOLO(modelPath)
    print(f"Model classes: {model.names}")

    results = model.predict(source=imagePath, conf=confidence, verbose=False)
    result = results[0]

    print(f"\nDetections on {imagePath} (conf >= {confidence}):")
    print("-" * 58)
    if len(result.boxes) == 0:
        print("  (none)")
    for box in result.boxes:
        className = model.names[int(box.cls)]
        conf = float(box.conf)
        x1, y1, x2, y2 = (round(v) for v in box.xyxy[0].tolist())
        print(f"  {className:<12} conf={conf:.3f}  box=({x1}, {y1}) -> ({x2}, {y2})")
    print("-" * 58)
    print(f"{len(result.boxes)} object(s) detected\n")

    result.save(filename=outputPath)
    print(f"Annotated image saved to: {os.path.abspath(outputPath)}")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", default=DEFAULT_IMAGE, help="Input image path")
    parser.add_argument("--output", default=DEFAULT_OUTPUT, help="Annotated output path")
    parser.add_argument("--alias", default=MODEL_ALIAS, help="LoadTools YOLO model alias")
    parser.add_argument("--conf", type=float, default=CONFIDENCE_THRESHOLD,
                        help="Confidence threshold")
    args = parser.parse_args()

    if not os.path.exists(args.image):
        sys.exit(f"Input image not found: {args.image}")

    modelPath = downloadModel(args.alias)
    runInference(modelPath, args.image, args.output, args.conf)


if __name__ == "__main__":
    main()
