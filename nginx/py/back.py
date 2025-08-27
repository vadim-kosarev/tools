# -*- coding: utf-8 -*-
import os
import re
import sqlite3
import subprocess
import sys
import time
from datetime import datetime
from fastapi import Response, status
import uvicorn
from fastapi import FastAPI, Request
from fastapi.openapi.models import Response
from starlette.responses import RedirectResponse

app = FastAPI()

DB_NAME = 'back.sqlite3'


def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS requests (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        token TEXT NOT NULL,
        phone TEXT,
        clientIP TEXT,
        userAgent TEXT,
        referer TEXT,
        status TEXT DEFAULT 'new',
        created_at INTEGER
    )
    ''')
    conn.commit()
    conn.close()


def get_current_time_millis():
    return int(time.time() * 1000)  # Умножаем на 1000, чтобы перевести секунды в миллисекунды


# Тестовый вывод для проверки текущего времени
print(f"Current timestamp in milliseconds: {get_current_time_millis()}")
print(
    f"Test date conversion from timestamp: {datetime.fromtimestamp(get_current_time_millis() / 1000).strftime('%Y-%m-%d %H:%M:%S')}")


def clean_phone_number(phone):
    # Просто удаляем все нецифровые символы, сохраняя все цифры включая код страны
    digits_only = re.sub(r'\D', '', phone)
    return digits_only


def format_phone_number(phone):
    if not phone or phone == 'phone=UNDEFINED':
        return phone

    if not re.match(r'^\d+$', phone):
        return phone

    if len(phone) == 10:
        return f"+7 ({phone[0:3]}) {phone[3:6]}-{phone[6:8]}-{phone[8:10]}"
    elif len(phone) == 11:
        country_code = phone[0]
        return f"+{country_code} ({phone[1:4]}) {phone[4:7]}-{phone[7:9]}-{phone[9:11]}"
    elif len(phone) > 11:
        if len(phone) > 13:
            country_code = phone[0:3]
            rest = phone[3:]
        elif len(phone) > 12:
            country_code = phone[0:2]
            rest = phone[2:]
        else:
            country_code = phone[0]
            rest = phone[1:]

        if len(rest) >= 10:
            return f"+{country_code} ({rest[0:3]}) {rest[3:6]}-{rest[6:8]}-{rest[8:10]}"
        else:
            return f"+{country_code} {rest}"
    else:
        return phone


def store_request_data(request, phone, headers):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    token = request.query_params.get('token', 'UndefToken')
    client_ip = headers.get('x-real-ip') or headers.get('x-forwarded-for') or headers.get('remote_addr')
    user_agent = headers.get('user-agent', '')
    referer = headers.get('referer', '')

    clean_phone = clean_phone_number(phone)

    cursor.execute('''
    INSERT INTO requests (token, phone, clientIP, userAgent, referer, created_at)
    VALUES (?, ?, ?, ?, ?, ?)
    ''', (token, clean_phone, client_ip, user_agent, referer, get_current_time_millis()))

    conn.commit()
    conn.close()
    return token


@app.middleware("http")
async def log_request(request: Request, call_next):
    body = await request.body()
    log_info = format_request_log(request, body)
    print(log_info)
    response = await call_next(request)
    return response


def format_request_log(request, body):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    info = []
    info.append(2 * "\n" + "=" * 50)
    info.append(f"[{timestamp}] {request.method} {request.url}")
    info.append("-" * 50)
    info.append("Headers:")
    for header, value in request.headers.items():
        info.append(f"  {header}: {value}")

    info.append("\nQuery Parameters:")
    for param, value in request.query_params.items():
        info.append(f"  {param}: {value}")

    if body:
        info.append("\nBody:")
        info.append(body.decode("utf-8"))

    info.append("\nClient:")
    info.append(f"  IP: {request.client.host}")
    info.append("=" * 50 + 2 * "\n")
    return "\n".join(info)


@app.get("/api/submit")
async def process_get(request: Request):
    return processRequest(request)


@app.post("/api/submit")
async def process_post(request: Request):
    return processRequest(request)


def processRequest(request: Request):
    log_info = format_request_log(request, None)

    aFile = f"data/{datetime.now().strftime('%Y.%m.%d-%H.%M.%S')}-{request.query_params.get('phone')}.txt"

    if not os.path.exists('data'):
        os.makedirs('data')

    phone = request.query_params.get('phone', 'phone=UNDEFINED')
    token = request.query_params.get('token', 'token=UNDEFINED')

    if phone != 'phone=UNDEFINED':
        token = store_request_data(request, phone, dict(request.headers))

    with open(f"{aFile}", "w") as file:
        file.write(f"""{phone}\n{token}""")

    if sys.platform.startswith('win'):
        subprocess.Popen(['notepad.exe', aFile], creationflags=subprocess.DETACHED_PROCESS)

    return {"message": phone, "token": token}


def get_filtered_requests(page: int = 1, page_size: int = 10, search: str = None, order: str = None,
                          timestamp_ms: int = None):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    query = "SELECT * FROM requests"
    params = []
    where_conditions = []

    if search:
        clean_search = re.sub(r'\D', '', search)

        if clean_search and len(clean_search) >= 4 and clean_search.isdigit():
            search_param = f"%{clean_search}%"
        else:
            search_param = f"%{search}%"

        search_condition = """(
            phone LIKE ? OR 
            token LIKE ? OR 
            clientIP LIKE ? OR 
            userAgent LIKE ? OR 
            referer LIKE ? OR 
            status LIKE ?
        )"""
        where_conditions.append(search_condition)
        params.extend([search_param] * 6)

    if timestamp_ms is not None:
        where_conditions.append("created_at >= ?")
        params.append(timestamp_ms)

    if where_conditions:
        query += " WHERE " + " AND ".join(where_conditions)

    if order:
        try:
            direction = ''
            if ':' in order:
                col, direction = order.split(':')
            else:
                col = order

            if direction.lower() != "desc":
                direction = 'asc'

            valid_columns = ['id', 'token', 'phone', 'clientIP', 'userAgent', 'referer', 'status', 'created_at']
            if col in valid_columns:
                query += f" ORDER BY {col} {direction.upper()}"
            else:
                query += " ORDER BY id DESC"
        except:
            query += " ORDER BY id DESC"
    else:
        query += " ORDER BY id DESC"

    # Save the query for count calculation before adding LIMIT and OFFSET
    count_query_base = query

    query += " LIMIT ? OFFSET ?"
    offset = (page - 1) * page_size
    params.extend([page_size, offset])

    # Calculate total count with the same WHERE conditions
    count_query = "SELECT COUNT(*) FROM requests"
    if where_conditions:
        count_query += " WHERE " + " AND ".join(where_conditions)

    count_params = params[:-2] if params else []  # Exclude the LIMIT and OFFSET parameters
    cursor.execute(count_query, count_params)
    total_count = cursor.fetchone()[0]

    print(f"Executing query: {query} with params: {params}")
    cursor.execute(query, params)
    columns = [description[0] for description in cursor.description]
    results = []
    for row in cursor.fetchall():
        item = dict(zip(columns, row))

        if 'phone' in item and item['phone']:
            item['phone'] = format_phone_number(item['phone'])

        results.append(item)

    conn.close()
    return {
        "total": total_count,
        "page": page,
        "page_size": page_size,
        "total_pages": (total_count + page_size - 1) // page_size,
        "data": results
    }


@app.get("/api/list")
async def list_requests(
        page: int = 1,
        page_size: int = 10,
        search: str = None,
        order: str = None,
        timestamp_ms: int = None
):
    return get_filtered_requests(page, page_size, search, order, timestamp_ms)

def checkAuth(request: Request):
    return True

@app.get("/auth-wifi/{path:path}")
async def auth_wifi(request: Request):
    orig_scheme = request.headers.get("x-original-scheme")
    orig_host = request.headers.get("x-original-host")
    target_uri = request.headers.get("X-Target-Uri")

    clientRequestUrl = f'{orig_scheme}://{orig_host}{request.url.path}'

    log_info = format_request_log(request, None)
    print(f"[AUTH WIFI] {log_info}")
    print(f"[AUTH WIFI] {clientRequestUrl}")

    isAuth = checkAuth(request)

    if isAuth:
        if "Android" in request.headers.get("user-agent", ""):
            # return 204 no content
            return Response(status_code=status.HTTP_204_NO_CONTENT)
        return "Success"


    redirect_url = f"{orig_scheme}://{orig_host}{target_uri}"

    print(f"[AUTH WIFI] {request.client.host} -> {redirect_url}")

    return RedirectResponse(
        url=redirect_url,
        status_code=307
    )


#########################################################################

init_db()

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=1501)
