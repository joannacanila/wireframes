# WIREFRAME model weights

This branch only hosts the model files that the WIREFRAME app downloads into `models/` on first start
(`app/core/weights.py` on `main`, which lists every file with its size and SHA-256). It shares no history with
`main`. Files over GitHub's 100 MB per-file limit are stored as numbered parts (`.000`, `.001`, ...); the app joins
them and checks the SHA-256 of the joined file.

| File | Size (bytes) | Model |
|---|---|---|
| `wirenet-r34.pt` | 97,918,915 | WireNet, U-Net with a ResNet-34 encoder (fusion member) |
| `v212_mitb1.pt` | 65,824,959 | WireNet with a MiT-B1 encoder (fusion member) |
| `v212_convnext_t.pt` (2 parts) | 127,821,839 | WireNet with a ConvNeXt-T encoder (fusion member) |
| `strand_head_v217.pt`, `strand_head_v217.json` | 9,975 / 279,817 | Strand type head (power vs telecom) and its feature statistics |
| `wireframe-yolo11m-seg.pt` | 20,660,928 | YOLO11s-seg fine-tuned at 1024 px (vegetation); the file name is historical |
| `poles-yolo11n-v218.pt` | 5,487,450 | YOLO11n utility-pole detector (context only) |
| `hf/dinov2-small/` | 88,249,960 (weights) | Copy of `facebook/dinov2-small` (Apache-2.0), used by the strand head |
| `hf/upernet-convnext-tiny/` (weights in 3 parts) | 241,053,050 (weights) | Copy of `openmmlab/upernet-convnext-tiny` (MIT), ADE20K scene parser |

## Licences and data

* WireNets and the strand head: trained on the team's Wireframes-Prelabelled-Datasets (CC BY 4.0); ResNet-34
  (torchvision, BSD-3), MiT / ConvNeXt encoders via segmentation-models-pytorch (MIT) and timm (Apache-2.0).
* Vegetation and pole detectors are fine-tuned from Ultralytics YOLO11 and are therefore AGPL-3.0.
* **Pole detector training data:** LVIS v1 `telephone_pole` annotations (CC BY 4.0) on COCO 2017 images (Flickr,
  each under its own Creative Commons licence, several non-commercial), starting from a detector trained on
  Zenodo 20091961 (CC BY 4.0, Google Street View captures). Google Street View imagery has its own terms of
  service; these weights are published by the project owner's decision for the team's academic inspection
  work. Do not use them commercially.
* DINOv2-small (Meta, Apache-2.0) and UperNet-ConvNeXt-T (OpenMMLab, MIT) are unmodified copies.
