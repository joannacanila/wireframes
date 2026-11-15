# WIREFRAME model weights

This branch only hosts the trained weights that the WIREFRAME app downloads on first start
(`app/core/weights.py` on `main`). It shares no history with `main`.

| File | Size (bytes) | SHA-256 | Model |
|---|---|---|---|
| `wirenet-r34.pt` | 97,918,915 | `ab2353c796f547ce1bae8e8aa02e26444e93d7d8ca77d991f0587ffe95e67525` | WireNet: U-Net with a ResNet-34 encoder (wire pixels) |
| `wireframe-yolo11m-seg.pt` | 20,660,928 | `1532d7ae9c74acab4bd66d13bd0b55786c950078a8548e1124442df4737d6652` | YOLO11s-seg fine-tuned at 1024 px (vegetation); the file name is historical |

Licence: the vegetation model is fine-tuned from Ultralytics YOLO11 and is therefore distributed under the
AGPL-3.0. WireNet builds on ResNet-34 (torchvision, BSD-3) and segmentation-models-pytorch (MIT).
The training data is the team's Wireframes-Prelabelled-Datasets (CC BY 4.0).
