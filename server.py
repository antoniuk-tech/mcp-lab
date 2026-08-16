import json
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

# Конфіг читається один раз при старті сервера.
# Path(__file__).parent — тека, де лежить сам server.py, тому шлях
# не залежить від того, з якої теки запущено процес.
BASE_DIR = Path(__file__).parent
THRESHOLDS = json.loads(
    (BASE_DIR / "config" / "thresholds.json").read_text(encoding="utf-8")
)

# Порядок важливий: індекс у списку = щабель суворості.
LEVELS = ["normal", "elevated", "severe", "critical"]


def fail(error_code: str, message: str, hint: str):
    """Єдине місце, де народжуються помилки інструмента.
    Текст помилки — JSON з трьох полів за розділом 1.3 контрактів."""
    raise ValueError(
        json.dumps(
            {"error_code": error_code, "message": message, "hint": hint},
            ensure_ascii=False,
        )
    )


class DayForecast(BaseModel):
    """Одна доба прогнозу на вході."""
    date: str
    temp_min_c: float
    wind_max_ms: float
    snowfall_mm: float = 0


class DayResult(BaseModel):
    """Одна доба на виході."""
    date: str
    base_severity: str
    flags: list[str]
    severity: str
    rationale: str


class SeverityReport(BaseModel):
    """Повна відповідь інструмента."""
    per_day: list[DayResult]
    overall_severity: str
    thresholds_version: str


mcp = FastMCP("kyiv-school-readiness")


@mcp.tool(
    annotations=ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    )
)
def edu_classify_weather_severity(days: list[DayForecast]) -> SeverityReport:
    """Класифікує погодні умови за рівнем ризику для роботи закладів освіти
    на основі добових показників прогнозу. Повертає клас суворості для кожної
    доби та підсумковий клас для періоду.
    Використовуй перед плануванням режиму навчання."""

    if len(days) == 0:
        fail("EMPTY_INPUT",
             "Список days порожній.",
             "Передай щонайменше одну добу прогнозу.")
    if len(days) > 5:
        fail("TOO_MANY_DAYS",
             f"Передано діб: {len(days)}.",
             "Максимум 5 діб. Скороти список.")

    temp_cfg = THRESHOLDS["temperature_c"]
    flags_cfg = THRESHOLDS["flags"]
    per_day = []

    for day in days:
        # Дата: перевіряємо формат, значення далі не використовується.
        try:
            datetime.strptime(day.date, "%Y-%m-%d")
        except ValueError:
            fail("INVALID_DATE_FORMAT",
                 f"Дата '{day.date}' не відповідає формату.",
                 "Очікується YYYY-MM-DD, наприклад 2026-12-14.")

        if not -60 <= day.temp_min_c <= 60:
            fail("IMPLAUSIBLE_VALUE",
                 f"temp_min_c = {day.temp_min_c} за {day.date} поза межами -60…60.",
                 "Перевір джерело даних.")
        if day.wind_max_ms < 0:
            fail("IMPLAUSIBLE_VALUE",
                 f"wind_max_ms = {day.wind_max_ms} за {day.date} від'ємний.",
                 "Швидкість вітру не може бути меншою за 0.")
        if day.snowfall_mm < 0:
            fail("IMPLAUSIBLE_VALUE",
                 f"snowfall_mm = {day.snowfall_mm} за {day.date} від'ємний.",
                 "Кількість опадів не може бути меншою за 0.")

        # Крок 1: базовий клас лише за температурою.
        # Пороги строгі: значення рівно на порозі належить м'якшому класу.
        if day.temp_min_c < temp_cfg["critical_below"]:
            base = "critical"
        elif day.temp_min_c < temp_cfg["severe_below"]:
            base = "severe"
        elif day.temp_min_c < temp_cfg["elevated_below"]:
            base = "elevated"
        else:
            base = "normal"

        # Крок 2: прапорці.
        flags = []
        if day.wind_max_ms >= flags_cfg["storm_wind_ms"]:
            flags.append("storm_wind")
        if day.snowfall_mm >= flags_cfg["heavy_snowfall_mm"]:
            flags.append("heavy_snowfall")

        # Крок 3: ескалація на один щабель, не вище critical.
        # min(...) не дає вийти за межі списку LEVELS.
        if flags:
            severity = LEVELS[min(LEVELS.index(base) + 1, len(LEVELS) - 1)]
        else:
            severity = base

        rationale = f"temp_min_c {day.temp_min_c} дає базовий клас {base}"
        if flags and severity != base:
            rationale += f"; прапорці {', '.join(flags)} підняли клас до {severity}"
        elif flags:
            rationale += f"; прапорці {', '.join(flags)} є, але вище critical клас не піднімається"

        per_day.append(DayResult(
            date=day.date,
            base_severity=base,
            flags=flags,
            severity=severity,
            rationale=rationale,
        ))

    # Крок 4: підсумок періоду — найгірший клас серед діб.
    overall = LEVELS[max(LEVELS.index(d.severity) for d in per_day)]

    return SeverityReport(
        per_day=per_day,
        overall_severity=overall,
        thresholds_version=THRESHOLDS["version"],
    )


if __name__ == "__main__":
    mcp.run()
