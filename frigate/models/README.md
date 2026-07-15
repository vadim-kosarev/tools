

yolov9s.onnx: https://huggingface.co/Kalray/yolov9/tree/main

config/model_cache/yolov9-t-320.onnx: собран `Dockerfile.yolov9-export` (официальный экспорт
из https://github.com/WongKinYiu/yolov9 через `export.py --simplify --include onnx`, без
ручного патчинга графа — в отличие от `transpon_model.py`/`yolov9t_transposed.onnx`, эта модель
полностью партиционируется на CUDAExecutionProvider и работает с ONNX-детектором Frigate на GPU
без crash-loop). Пересборка:
```powershell
docker build . --build-arg MODEL_SIZE=t --build-arg IMG_SIZE=320 --output . -f Dockerfile.yolov9-export
```
