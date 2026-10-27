import urllib.parse
import urllib.request
import json

def run(ctx):
    # Если пользователь ничего не указал после команды (например, просто написал !погода)
    if not ctx.args:
        ctx.reply(f"@{ctx.user}, укажите город. Пример: !погода Лондон")
        return

    # Собираем название города из аргументов (поддерживает города из нескольких слов, например, Новый Йорк)
    city = " ".join(ctx.args).strip()
    
    # Кодируем название города для URL (чтобы кириллица и пробелы корректно передались)
    encoded_city = urllib.parse.quote(city)
    
    # Формируем URL к wttr.in с форматом j1 (JSON) и языком ru
    url = f"https://wttr.in/{encoded_city}?format=j1&lang=ru"
    
    headers = {
        # Добавляем User-Agent, чтобы сервис корректно ответил
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
    }

    try:
        req = urllib.request.Request(url, headers=headers)
        # Устанавливаем таймаут 7 секунд, чтобы уложиться в 10-секундный лимит OpenBot
        with urllib.request.urlopen(req, timeout=7) as response:
            if response.status != 200:
                ctx.reply(f"@{ctx.user}, не удалось найти город «{city}».")
                return

            data = json.loads(response.read().decode("utf-8"))
            
            # Извлекаем текущие данные о погоде
            current = data["current_condition"][0]
            
            temp = current.get("temp_C", "?")
            feels_like = current.get("FeelsLikeC", "?")
            humidity = current.get("humidity", "?")
            wind_speed = current.get("windspeedKmph", "?")

            # Описание погоды на русском (при наличии)
            lang_ru = current.get("lang_ru")
            if lang_ru and len(lang_ru) > 0:
                condition = lang_ru[0].get("value", "Неизвестно").lower()
            else:
                condition = "нет данных"

            # Определяем точное название найденного места
            nearest_area = data.get("nearest_area", [{}])[0]
            area_name = nearest_area.get("areaName", [{}])[0].get("value", city)
            country = nearest_area.get("country", [{}])[0].get("value", "")
            
            location = f"{area_name}, {country}" if country else area_name

            # Формируем и отправляем ответ в чат
            ctx.reply(
                f" Погода в {location}: {condition}, {temp}°C (ощущается как {feels_like}°C), "
                f"ветер {wind_speed} км/ч, влажность {humidity}%."
            )

    except Exception:
        ctx.reply(f"@{ctx.user}, не удалось получить погоду для «{city}». Проверьте название города.")