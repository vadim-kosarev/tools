import cv2
import numpy as np
import os
from glob import glob

# Пути
input_folder = 'png600dpi'
output_folder = 'png_out'
watermark_path = 'WATERMARK.png'

# Создать выходную папку, если не существует
os.makedirs(output_folder, exist_ok=True)

# Загрузить ватермарк
watermark = cv2.imread(watermark_path).astype(np.float32)

# Маска: где ватермарк НЕ белый
tolerance = 1e-3
wm_mask = np.any(watermark < 255 - tolerance, axis=2)
wm_mask_3ch = np.repeat(wm_mask[:, :, np.newaxis], 3, axis=2)

# Найти все PNG-файлы в папке
image_files = glob(os.path.join(input_folder, '*.png'))

# Обработка всех файлов
for img_path in image_files:
    filename = os.path.basename(img_path)
    print(f'Обрабатываем: {filename}')

    # Загрузить изображение
    img_result = cv2.imread(img_path).astype(np.float32)

    # Проверка размеров
    if img_result.shape != watermark.shape:
        print(f'❌ Пропущено: размеры не совпадают — {filename}')
        continue

    # Восстановление
    epsilon = 1e-5
    restored = img_result.copy()
    restored[wm_mask_3ch] = 255.0 * img_result[wm_mask_3ch] / (watermark[wm_mask_3ch] + epsilon)
    restored = np.clip(restored, 0, 255)

    # Сохранение
    out_path = os.path.join(output_folder, filename)
    cv2.imwrite(out_path, restored.astype(np.uint8))

print('✅ Готово.')
