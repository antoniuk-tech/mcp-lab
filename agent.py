"""Агент: MCP-клієнт до двох серверів + цикл з LLM.

Три процеси:
  1) цей файл — агент і клієнт;
  2) server.py — власний сервер kyiv-school-readiness (3 інструменти);
  3) mcp-weather — зовнішній сервер OpenWeather (1 інструмент).

Обидва сервери спілкуються через stdio: агент запускає їх як дочірні
процеси і пише в їхній стандартний ввід.
"""

import asyncio
import os
from pathlib import Path

from dotenv import load_dotenv
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

BASE_DIR = Path(__file__).parent
load_dotenv(BASE_DIR / ".env")

OWM_API_KEY = os.getenv("OWM_API_KEY")
WEATHER_SERVER = Path.home() / "dev" / "external" / "mcp-openweather" / "mcp-weather"


def own_server_params() -> StdioServerParameters:
    """Власний сервер: запускається інтерпретатором з venv.
    sys.executable не використовуємо навмисно — шлях явний і читабельний."""
    return StdioServerParameters(
        command=str(BASE_DIR / ".venv" / "bin" / "python"),
        args=[str(BASE_DIR / "server.py")],
    )


def weather_server_params() -> StdioServerParameters:
    """Зовнішній сервер: скомпільований бінарник на Go.
    Ключ передається саме тут, через env дочірнього процесу — інспектор
    цього не робив, тому й повертав нулі."""
    return StdioServerParameters(
        command=str(WEATHER_SERVER),
        args=[],
        env={"OWM_API_KEY": OWM_API_KEY or ""},
    )


async def main():
    if not OWM_API_KEY:
        print("Немає OWM_API_KEY у .env")
        return
    if not WEATHER_SERVER.exists():
        print(f"Не знайдено бінарник сервера погоди: {WEATHER_SERVER}")
        return

    # Два вкладені підключення: обидва сервери живуть, доки триває блок.
    async with stdio_client(own_server_params()) as (own_read, own_write):
        async with ClientSession(own_read, own_write) as own:
            await own.initialize()
            own_tools = (await own.list_tools()).tools

            async with stdio_client(weather_server_params()) as (w_read, w_write):
                async with ClientSession(w_read, w_write) as weather:
                    await weather.initialize()
                    weather_tools = (await weather.list_tools()).tools

                    print(f"\nВласний сервер — інструментів: {len(own_tools)}")
                    for tool in own_tools:
                        print(f"  {tool.name}")

                    print(f"\nСервер погоди — інструментів: {len(weather_tools)}")
                    for tool in weather_tools:
                        print(f"  {tool.name}")

                    # Пробний виклик чужого сервера: перевіряємо, що ключ
                    # доїхав і місто розпізналося.
                    result = await weather.call_tool("weather", {"city": "Kyiv"})
                    text = result.content[0].text
                    print(f"\nВідповідь про погоду — символів: {len(text)}")
                    print(text[:200])


if __name__ == "__main__":
    asyncio.run(main())
