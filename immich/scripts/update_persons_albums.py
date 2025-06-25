import json
import logging
from logging.handlers import RotatingFileHandler
from typing import List, Dict, Optional

import requests
from requests.adapters import HTTPAdapter
from tqdm import tqdm
from urllib3.util.retry import Retry

# Конфигурация
IMMICH_API_URL = "http://brightsky:2283/api"
API_KEY = "39JmYw9mAA7jFUyuRTWgmbOzizBTOXnaYe1l8xgTw"
PERSON_NAMES = ["Vlad Kosarev", "Seva Kosarev", "Natalia Kosareva", "Andrey Napalkov", "Vadim Kosarev",
                "Vsevolod Fadeev", "Maria Sitnova", "Lisa Sitnova", "Polina Sitnova"]  # Список имен людей
MAX_ASSETS = "all"  # Максимум активов для обработки (100 или "all")
PAGE_SIZE = 1000  # Размер страницы для запроса активов

TRACE = 5


# Настройка логирования с ротацией и выводом в консоль
def configure_logger(logging_level: int = logging.INFO):
    class UpToInfoFilter(logging.Filter):
        def filter(self, record):
            return record.levelno <= logging.INFO

    class UpToDebugFilter(logging.Filter):
        def filter(self, record):
            return record.levelno <= logging.DEBUG

    handlers = []

    # Файл-логгер: все уровни, включая DEBUG
    file_handler = RotatingFileHandler("immich_requests.log", maxBytes=50 * 1024 * 1024, backupCount=5)
    file_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
    file_handler.setLevel(logging.DEBUG)
    file_handler.addFilter(UpToDebugFilter())
    handlers.append(file_handler)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
    console_handler.setLevel(logging.INFO)
    console_handler.addFilter(UpToInfoFilter())
    handlers.append(console_handler)

    logging.basicConfig(
        level=logging_level,
        handlers=handlers
    )
    # Добавляем уровень TRACE (ниже DEBUG)
    logging.addLevelName(TRACE, "TRACE")


def trace(self, message, *args, **kws):
    isTraceEnabled = self.isEnabledFor(TRACE)
    if isTraceEnabled:
        self._log(TRACE, message, args, **kws)


logging.Logger.trace = trace

configure_logger(logging_level=TRACE)
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
        # Логируем запрос только на уровне TRACE
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
        logger.trace(log_message)

        response = super().send(request, **kwargs)

        log_message = (
            f"HTTP Response: {request.method} {request.url}\n"
            f"Status Code: {response.status_code}\n"
        )
        try:
            response_json = response.json()
            log_message += f"Response Body: {json.dumps(response_json, indent=2)}\n"
        except json.JSONDecodeError:
            log_message += f"Response Body: {response.text}\n"
        logger.trace(log_message)

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
    logger.info(f"Searching for person '{person_name}'...")
    response = session.get(f"{IMMICH_API_URL}/people")
    response.raise_for_status()
    people = response.json().get("people", [])
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
    logger.info("Fetching assets...")
    while True:
        response = session.post(f"{IMMICH_API_URL}/search/metadata", json=payload)
        response.raise_for_status()
        search_results = response.json()
        page_assets = search_results.get("assets", {}).get("items", [])
        assets.extend(page_assets)
        if not search_results.get("assets", {}).get("nextPage"):
            break
        payload["page"] += 1

    # Сортировка по fileCreatedAt (последние фотографии) и ограничение
    if max_assets != "all" and isinstance(max_assets, int):
        assets = sorted(assets, key=lambda x: x.get("fileCreatedAt", ""), reverse=True)[:max_assets]
        logger.info(f"Limited to the {max_assets} most recent assets.")

    return assets


def find_album_by_name(album_name: str) -> Optional[Dict]:
    """Найти альбом по имени."""
    logger.info(f"Searching for album '{album_name}'...")
    response = session.get(f"{IMMICH_API_URL}/albums")
    response.raise_for_status()
    albums = response.json()
    for album in albums:
        if album.get("albumName") == album_name:
            return album
    return None


def create_album(album_name: str, asset_ids: List[str]) -> str:
    """Создать новый альбом с указанными активами."""
    logger.info(f"Creating new album '{album_name}'...")
    payload = {
        "albumName": album_name,
        "assetIds": asset_ids
    }
    response = session.post(f"{IMMICH_API_URL}/albums", json=payload)
    response.raise_for_status()
    album = response.json()
    logger.info(f"Created album '{album_name}' with ID: {album['id']}")
    return album["id"]


def add_assets_to_album(album_id: str, asset_ids: List[str]) -> None:
    """Добавить активы в альбом."""
    if not asset_ids:
        return
    logger.info(f"Adding {len(asset_ids)} assets to album...")
    payload = {"ids": asset_ids}
    response = session.put(f"{IMMICH_API_URL}/albums/{album_id}/assets", json=payload)
    response.raise_for_status()
    logger.info(f"Added {len(asset_ids)} assets to album {album_id}")


def get_album_assets(album_id: str) -> List[Dict]:
    """Получить список активов в альбоме."""
    logger.info(f"Fetching assets for album ID: {album_id}...")
    response = session.get(f"{IMMICH_API_URL}/albums/{album_id}")
    response.raise_for_status()
    album_data = response.json()
    assets = album_data.get("assets", [])
    return assets


def process_person(person_name: str) -> None:
    """Обработать одного человека: найти фотографии и добавить в альбом."""
    try:
        # Найти ID человека по имени
        person_id = get_person_id_by_name(person_name)
        if not person_id:
            logger.error(f"Person '{person_name}' not found.")
            return
        logger.info(f"Found person '{person_name}' with ID: {person_id}")

        # Получить активы с этим человеком
        assets = get_all_assets(person_id, MAX_ASSETS)
        if not assets:
            logger.warning(f"No assets found with person '{person_name}'.")
            return
        asset_ids = [asset["id"] for asset in assets]
        logger.info(f"Found {len(asset_ids)} assets with person '{person_name}'.")

        # Найти или создать альбом
        album_name = person_name  # Имя альбома = имя человека
        album = find_album_by_name(album_name)
        if album:
            album_id = album["id"]
            logger.info(f"Found existing album '{album_name}' with ID: {album_id}")
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
            logger.info(f"No new assets to add to album '{album_name}'.")

    except requests.exceptions.RequestException as e:
        logger.error(f"Error processing person '{person_name}': {e}")


def main(logging_level: int = logging.INFO):
    try:
        configure_logger(logging_level=logging_level)

        # Обработать каждого человека из списка
        for person_name in tqdm(PERSON_NAMES, desc="Processing persons", unit="person"):
            logger.info(f"=== Processing person: {person_name} ===")
            process_person(person_name)

    except Exception as e:
        logger.error(f"Unexpected error: {e}")


if __name__ == "__main__":
    main(logging_level=TRACE)
