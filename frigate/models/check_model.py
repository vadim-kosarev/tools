import onnx
import onnxruntime as ort
import numpy as np
from pathlib import Path

def check_onnx_model(model_path: str):
    model_path = Path(model_path)
    if not model_path.exists():
        raise FileNotFoundError(f"Файл не найден: {model_path}")

    print(f"🔍 Проверка ONNX модели: {model_path}")

    # --- 1️⃣ Проверка структуры модели
    try:
        model = onnx.load(model_path)
        onnx.checker.check_model(model)
        print("✅ Модель успешно загружена и прошла структурную проверку ONNX.")
    except Exception as e:
        print("❌ Ошибка в структуре модели:", e)
        return

    # --- 2️⃣ Информация о входах и выходах
    print("\n📦 Входы модели:")
    for i, inp in enumerate(model.graph.input):
        shape = []
        for dim in inp.type.tensor_type.shape.dim:
            shape.append(dim.dim_value if dim.dim_value != 0 else "dynamic")
        dtype = inp.type.tensor_type.elem_type
        print(f"  [{i}] {inp.name} | dtype={dtype} | shape={shape}")

    print("\n📤 Выходы модели:")
    for o, out in enumerate(model.graph.output):
        shape = []
        for dim in out.type.tensor_type.shape.dim:
            shape.append(dim.dim_value if dim.dim_value != 0 else "dynamic")
        dtype = out.type.tensor_type.elem_type
        print(f"  [{o}] {out.name} | dtype={dtype} | shape={shape}")

    # --- 3️⃣ Проверка доступных провайдеров
    providers = ort.get_available_providers()
    print("\n⚙️ Доступные backend-провайдеры:", providers)

    # --- 4️⃣ Пробный инференс
    try:
        sess = ort.InferenceSession(str(model_path), providers=providers)
        input_name = sess.get_inputs()[0].name
        input_shape = sess.get_inputs()[0].shape
        input_dtype = sess.get_inputs()[0].type

        # Генерация фиктивного входа
        shape_fixed = [s if isinstance(s, int) and s > 0 else 640 for s in input_shape]
        dummy = np.random.rand(*shape_fixed).astype(np.float32)

        print(f"\n🚀 Пробный инференс: вход {input_name} {shape_fixed}")
        outputs = sess.run(None, {input_name: dummy})
        print(f"✅ Успешно! Количество выходов: {len(outputs)}")

        for idx, out in enumerate(outputs):
            print(f"  └─ Output[{idx}]: shape={out.shape}, dtype={out.dtype}")

    except Exception as e:
        print("\n❌ Ошибка при инференсе модели:", e)

    print("\n🧾 Проверка завершена.")


if __name__ == "__main__":
    import sys
    if len(sys.argv) != 2:
        print("Использование: python check_onnx_model.py path/to/model.onnx")
    else:
        check_onnx_model(sys.argv[1])
