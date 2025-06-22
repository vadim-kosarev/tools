import requests
import logging
from logging.handlers import RotatingFileHandler
import json
from typing import List, Dict, Optional
from uuid import UUID
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from tqdm import tqdm
from datetime import datetime

# Конфигурация
IMMICH_API_URL = "http://brightsky:2283/api"
API_KEY = "39JmYw9mAA7jFUyuRTWgmbOzizBTOXnaYe1l8xgTw"
PERSON_NAMES = ["Vlad Kosarev", "Seva Kosarev", "Natalia Kosareva", "Andrey Napalkov", "Vadim Kosarev", "Vsevolod Fadeev", "Maria Sitnova", "Lisa Sitnova", "Polina Sitnova"]  # Список имен людей
MAX_ASSETS = "all"  # Максимум активов для обработки (100 или "all")
PAGE_SIZE = 1000  # Размер страницы для запроса активов

# Настройка логирования с ротацией
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        RotatingFileHandler(
            "immich_requests.log",
            maxBytes=50 * 1024 * 1024,  # 50 МБ
            backupCount=5  # До 5 резервных файлов
        )
        # logging.StreamHandler() отключен для консоли, можно включить для дебага
    ]
)
logger = logging.getLogger(__name__)

# Заголовки для аутентификации
headers = {
    "x-api-key": API_KEY,
    "Accept": "application/json",
    "Content-Type": "application/json"
}

# Кастомный HTTP-адаптер для логирования запросов
class LoggingHTTPAdapter(HTTPAdapter):
    def send(self, request, **kwargs):
        # Логируем запрос
        log_message = (
            f"HTTP Request: {request.method} {request.url}\n"
            f"Headers: {json.dumps(dict(request.headers), indent=2)}\n"
        )
        if request.body:
            try:
                body = json.loads(request.body) if isinstance(request.body, bytes) else request.body
                log_message += f"Body: {json.dumps(body, indent=2)}\n"
            except (json.JSONDecodeError, TypeError):
                log_message += f"Body: {request.body}\n"
        logger.info(log_message)

        # Выполняем запрос
        response = super().send(request, **kwargs)

        # Логируем ответ
        log_message = (
            f"HTTP Response: {request.method} {request.url}\n"
            f"Status Code: {response.status_code}\n"
        )
        try:
            response_json = response.json()
            log_message += f"Response Body: {json.dumps(response_json, indent=2)}\n"
        except json.JSONDecodeError:
            log_message += f"Response Body: {response.text}\n"
        logger.info(log_message)

        return response

# Настройка сессии с повторными попытками и логированием
session = requests.Session()
retries = Retry(total=3, backoff_factor=0.5, status_forcelist=[429, 500, 502, 503, 504])
session.mount("http://", LoggingHTTPAdapter(max_retries=retries))
session.mount("https://", LoggingHTTPAdapter(max_retries=retries))
session.headers.update(headers)

def enable_console_logging():
    """Включить логирование в консоль для дебага."""
    logger.handlers.append(logging.StreamHandler())
    logger.info("Console logging enabled for debugging.")

def get_person_id_by_name(person_name: str) -> Optional[str]:
    """Найти ID человека по его имени."""
    print(f"Searching for person '{person_name}'...")
    response = session.get(f"{IMMICH_API_URL}/people")
    response.raise_for_status()
    people = response.json().get("people", [])
    #for person in tqdm(people, desc=f"Checking people for {person_name}", unit="person"):
    for person in people:
        if person.get("name") == person_name:
            return person.get("id")
    return None

def get_all_assets(person_id: str, max_assets: int | str = MAX_ASSETS) -> List[Dict]:
    """Получить активы из таймлайна, где присутствует указанный человек."""
    payload = {
        "personIds": [person_id],
        "page": 1,
        "size": PAGE_SIZE,  # Размер страницы
        "withExif": True,
        "isVisible": True
    }
    assets = []
    print("Fetching assets...")
    with tqdm(desc="Fetching asset pages", unit="page") as page_pbar:
        while True:
            response = session.post(f"{IMMICH_API_URL}/search/metadata", json=payload)
            response.raise_for_status()
            search_results = response.json()
            page_assets = search_results.get("assets", {}).get("items", [])
            assets.extend(page_assets)
            page_pbar.update(1)
            if not search_results.get("assets", {}).get("nextPage"):
                break
            payload["page"] += 1

    # Сортировка по fileCreatedAt (последние фотографии) и ограничение
    if max_assets != "all" and isinstance(max_assets, int):
        assets = sorted(assets, key=lambda x: x.get("fileCreatedAt", ""), reverse=True)[:max_assets]
        print(f"Limited to the {max_assets} most recent assets.")

    return assets

def find_album_by_name(album_name: str) -> Optional[Dict]:
    """Найти альбом по имени."""
    print(f"Searching for album '{album_name}'...")
    response = session.get(f"{IMMICH_API_URL}/albums")
    response.raise_for_status()
    albums = response.json()
    # for album in tqdm(albums, desc=f"Checking albums for {album_name}", unit="album"):
    for album in albums:
        if album.get("albumName") == album_name:
            return album
    return None

def create_album(album_name: str, asset_ids: List[str]) -> str:
    """Создать новый альбом с указанными активами."""
    print(f"Creating new album '{album_name}'...")
    payload = {
        "albumName": album_name,
        "assetIds": asset_ids
    }
    response = session.post(f"{IMMICH_API_URL}/albums", json=payload)
    response.raise_for_status()
    album = response.json()
    print(f"Created album '{album_name}' with ID: {album['id']}")
    return album["id"]

def add_assets_to_album(album_id: str, asset_ids: List[str]) -> None:
    """Добавить активы в альбом."""
    if not asset_ids:
        return
    print(f"Adding {len(asset_ids)} assets to album...")
    payload = {"ids": asset_ids}
    with tqdm(total=len(asset_ids), desc="Adding assets", unit="asset") as pbar:
        response = session.put(f"{IMMICH_API_URL}/albums/{album_id}/assets", json=payload)
        response.raise_for_status()
        pbar.update(len(asset_ids))
    print(f"Added {len(asset_ids)} assets to album {album_id}")

def get_album_assets(album_id: str) -> List[Dict]:
    """Получить список активов в альбоме."""
    print(f"Fetching assets for album ID: {album_id}...")
    response = session.get(f"{IMMICH_API_URL}/albums/{album_id}")
    response.raise_for_status()
    album_data = response.json()
    assets = album_data.get("assets", [])
    with tqdm(total=len(assets), desc="Processing album assets", unit="asset") as pbar:
        for _ in assets:
            pbar.update(1)
    return assets

def process_person(person_name: str) -> None:
    """Обработать одного человека: найти фотографии и добавить в альбом."""
    try:
        # Найти ID человека по имени
        person_id = get_person_id_by_name(person_name)
        if not person_id:
            print(f"Error: Person '{person_name}' not found.")
            logger.error(f"Person '{person_name}' not found.")
            return
        print(f"Found person '{person_name}' with ID: {person_id}")

        # Получить активы с этим человеком
        assets = get_all_assets(person_id, MAX_ASSETS)
        if not assets:
            print(f"No assets found with person '{person_name}'.")
            logger.warning(f"No assets found with person '{person_name}'.")
            return
        asset_ids = [asset["id"] for asset in assets]
        print(f"Found {len(asset_ids)} assets with person '{person_name}'.")

        # Найти или создать альбом
        album_name = person_name  # Имя альбома = имя человека
        album = find_album_by_name(album_name)
        if album:
            album_id = album["id"]
            print(f"Found existing album '{album_name}' with ID: {album_id}")
        else:
            album_id = create_album(album_name, asset_ids)
            return  # Новый альбом уже содержит все нужные активы

        # Получить текущие активы альбома
        current_assets = get_album_assets(album_id)
        current_asset_ids = {asset["id"] for asset in current_assets}

        # Определить новые активы для добавления
        assets_to_add = [asset_id for asset_id in asset_ids if asset_id not in current_asset_ids]

        # Добавить новые активы
        if assets_to_add:
            add_assets_to_album(album_id, assets_to_add)
        else:
            print(f"No new assets to add to album '{album_name}'.")

    except requests.exceptions.RequestException as e:
        print(f"Error processing person '{person_name}': {e}")
        logger.error(f"Error processing person '{person_name}': {e}")

def main(debug: bool = False):
    try:
        if debug:
            enable_console_logging()

        # Обработать каждого человека из списка
        for person_name in tqdm(PERSON_NAMES, desc="Processing persons", unit="person"):
            print(f"\n\n=== Processing person: {person_name} ===\n")
            process_person(person_name)

    except Exception as e:
        print(f"Unexpected error: {e}")
        logger.error(f"Unexpected error: {e}")

if __name__ == "__main__":
    main(debug=False)  # Установите debug=True для вывода логов в консоль