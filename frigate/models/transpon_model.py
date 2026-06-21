import onnx
from onnx import helper, numpy_helper, shape_inference

# Загрузить исходную модель
model = onnx.load("yolov9t.onnx")

# Добавить узел транспонирования
transpose_node = helper.make_node(
    "Transpose",
    inputs=["predictions"],      # имя выходного тензора модели
    outputs=["output0"],         # новое имя выхода
    perm=[0, 2, 1]               # переставляем оси: (1,84,8400) -> (1,8400,84)
)

# Добавить этот узел в конец графа
model.graph.node.append(transpose_node)

# Обновить выход модели
model.graph.output[0].name = "output0"

# (не обязательно, но можно прогнать инференс форм)
model = shape_inference.infer_shapes(model)

onnx.save(model, "yolovts_transposed.onnx")
print("✅ Модель сохранена: yolov9t_transposed.onnx")
