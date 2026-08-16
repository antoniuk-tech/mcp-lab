"""Агент: MCP-клієнт до двох серверів + цикл з LLM.

Три процеси:
  1) цей файл — агент, MCP-клієнт і цикл із моделлю;
  2) server.py — власний сервер kyiv-school-readiness (3 інструменти);
  3) mcp-weather — зовнішній сервер OpenWeather (1 інструмент).

Поділ праці: модель вирішує, які інструменти викликати й у якому
порядку, і формулює висновок. Арифметику й розбір тексту робить код —
у моделі вони ненадійні.
"""

import argparse
import asyncio
import json
import os
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

BASE_DIR = Path(__file__).parent
load_dotenv(BASE_DIR / ".env")

OWM_API_KEY = os.getenv("OWM_API_KEY")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
WEATHER_SERVER = Path.home() / "dev" / "external" / "mcp-openweather" / "mcp-weather"
MODEL = "openai/gpt-4o"
MAX_STEPS = 8

SYSTEM_PROMPT = """Ти — помічник управління освіти Києва. Твоє завдання:
за прогнозом погоди визначити, які школи переводити на дистанційне навчання
і куди спрямувати обмежену кількість резервних генераторів.

Порядок роботи:
1. get_forecast — прогноз на 3 доби (назва міста лише англійською).
2. edu_classify_weather_severity — клас суворості за цим прогнозом.
3. edu_assess_school_readiness — картина готовності за отриманим класом.
4. edu_plan_learning_mode — план і розподіл генераторів.

Значення severity для кроків 3 і 4 бери з overall_severity кроку 2,
не вигадуй його сам. Нічого не рахуй самостійно: усі числа — з інструментів.
Якщо інструмент повернув помилку з полем hint — виправ виклик за підказкою.

Наприкінці дай коротку відповідь українською: клас суворості й чому,
скільки шкіл очно й дистанційно, скільки учнів 1-4 класів на дистанційці,
кому саме віддано генератори. Якщо в даних про прогноз є попередження —
обов'язково повтори його у відповіді."""


def parse_forecast(text: str, limit: int = 3) -> dict:
    """Перетворює текстову відповідь сервера погоди на подобові показники.

    Сервер віддає прогноз слотами по 3 години, а нашій класифікації
    потрібна доба. Тому групуємо слоти за датою і беремо мінімальний Low.

    Двох величин у джерелі немає взагалі:
      wind_max_ms — є лише в блоці поточної погоди, беремо звідти як наближення;
      snowfall_mm — відсутній, ставимо 0 і чесно про це попереджаємо."""

    wind = None
    lows_by_date = {}
    current_date = None

    for raw in text.splitlines():
        line = raw.strip()
        if wind is None and line.startswith("Wind Speed:"):
            wind = float(line.split(":", 1)[1].strip())
        elif line.startswith("Date & Time:"):
            current_date = line.split(":", 1)[1].strip().split()[0]
        elif line.startswith("Low:") and current_date:
            value = float(line.split(":", 1)[1].strip())
            lows_by_date.setdefault(current_date, []).append(value)

    if not lows_by_date:
        raise ValueError("у відповіді сервера погоди немає жодного запису прогнозу")

    days = [
        {
            "date": day,
            "temp_min_c": min(lows_by_date[day]),
            "wind_max_ms": wind or 0.0,
            "snowfall_mm": 0.0,
        }
        for day in sorted(lows_by_date)[:limit]
    ]

    return {
        "days": days,
        "notes": (
            "wind_max_ms узято з поточної погоди — подобового прогнозу вітру "
            "джерело не дає; snowfall_mm = 0, бо опадів у міліметрах джерело "
            "не повертає"
        ),
    }


def mcp_tools_to_openai(tools) -> list[dict]:
    """Переклад опису інструментів з формату MCP у формат, який розуміє модель.
    Опис і схема беруться з сервера — руками нічого не дублюється."""
    return [
        {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description or "",
                "parameters": tool.inputSchema,
            },
        }
        for tool in tools
    ]


FORECAST_TOOL = {
    "type": "function",
    "function": {
        "name": "get_forecast",
        "description": (
            "Прогноз погоди на 3 доби: мінімальна температура, вітер, опади. "
            "Назва міста лише англійською, наприклад Kyiv."
        ),
        "parameters": {
            "type": "object",
            "properties": {"city": {"type": "string"}},
            "required": ["city"],
        },
    },
}


async def call_mcp(session: ClientSession, name: str, arguments: dict) -> str:
    """Виклик інструмента MCP. Помилку не ховаємо і не перехоплюємо:
    текст помилки разом із hint повертається моделі, щоб вона виправилась."""
    result = await session.call_tool(name, arguments)
    text = "\n".join(part.text for part in result.content if part.type == "text")
    if result.isError:
        return f"ПОМИЛКА ІНСТРУМЕНТА: {text}"
    return text


async def run(city: str, generators: int, assumed: str | None):
    if not OPENROUTER_API_KEY:
        print("Немає OPENROUTER_API_KEY у .env")
        return
    if not WEATHER_SERVER.exists():
        print(f"Не знайдено бінарник сервера погоди: {WEATHER_SERVER}")
        return

    llm = OpenAI(api_key=OPENROUTER_API_KEY, base_url="https://openrouter.ai/api/v1")

    own_params = StdioServerParameters(
        command=str(BASE_DIR / ".venv" / "bin" / "python"),
        args=[str(BASE_DIR / "server.py")],
    )
    weather_params = StdioServerParameters(
        command=str(WEATHER_SERVER),
        args=[],
        env={"OWM_API_KEY": OWM_API_KEY or ""},
    )

    async with stdio_client(own_params) as (own_r, own_w):
        async with ClientSession(own_r, own_w) as own:
            await own.initialize()
            own_tools = (await own.list_tools()).tools

            async with stdio_client(weather_params) as (w_r, w_w):
                async with ClientSession(w_r, w_w) as weather:
                    await weather.initialize()

                    tools = mcp_tools_to_openai(own_tools) + [FORECAST_TOOL]

                    task = (
                        f"Місто: {city}. Доступно генераторів: {generators}. "
                        f"Побудуй план режиму навчання."
                    )
                    if assumed:
                        task += (
                            f" УВАГА: прогноз використовувати не можна, "
                            f"інструмент get_forecast не викликай. "
                            f"Клас суворості заданий вручну: {assumed}. "
                            f"У відповіді обов'язково зазнач, що план побудований "
                            f"на припущенні, а не на фактичному прогнозі."
                        )

                    messages = [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": task},
                    ]

                    for step in range(MAX_STEPS):
                        response = llm.chat.completions.create(
                            model=MODEL, messages=messages, tools=tools
                        )
                        message = response.choices[0].message
                        messages.append(message.model_dump(exclude_none=True))

                        if not message.tool_calls:
                            print("\n--- Відповідь агента ---\n")
                            print(message.content)
                            return

                        for call in message.tool_calls:
                            name = call.function.name
                            args = json.loads(call.function.arguments or "{}")
                            print(f"[крок {step + 1}] {name} {args}")

                            if name == "get_forecast":
                                # Єдиний виклик зовнішнього сервера. Тут же
                                # реалізовано розділ 9: збій не валить агента,
                                # а перетворюється на явне припущення.
                                try:
                                    raw = await call_mcp(
                                        weather, "weather", {"city": args.get("city", city)}
                                    )
                                    if raw.startswith("ПОМИЛКА"):
                                        raise ValueError(raw)
                                    content = json.dumps(
                                        parse_forecast(raw), ensure_ascii=False
                                    )
                                except Exception as error:
                                    print(f"    прогноз недоступний: {error}")
                                    content = json.dumps({
                                        "error": str(error),
                                        "fallback_severity": "severe",
                                        "warning": (
                                            "Прогноз недоступний. Використай "
                                            "severity=severe як консервативне "
                                            "припущення і попередь користувача, що "
                                            "план побудований не на фактичних даних."
                                        ),
                                    }, ensure_ascii=False)
                            else:
                                content = await call_mcp(own, name, args)

                            preview = content[:120].replace("\n", " ")
                            print(f"    -> {preview}...")

                            messages.append({
                                "role": "tool",
                                "tool_call_id": call.id,
                                "content": content,
                            })

                    print("Досягнуто ліміту кроків без підсумкової відповіді.")


def main():
    parser = argparse.ArgumentParser(description="Агент планування режиму навчання")
    parser.add_argument("--city", default="Kyiv")
    parser.add_argument("--generators", type=int, default=2)
    parser.add_argument(
        "--assume-severity",
        choices=["normal", "elevated", "severe", "critical"],
        help="задати клас суворості вручну, без звернення до прогнозу",
    )
    args = parser.parse_args()
    asyncio.run(run(args.city, args.generators, args.assume_severity))


if __name__ == "__main__":
    main()
