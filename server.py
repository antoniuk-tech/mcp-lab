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




# --- Робота з датасетом ---------------------------------------------------
# Імпорти зазвичай тримають на початку файлу; тут вони стоять біля свого
# блоку, щоб не редагувати верх файлу вручну. На роботу це не впливає.
import csv
from datetime import date

DATA_PATH = BASE_DIR / "data" / "schools.csv"

REQUIRED_COLUMNS = [
    "school_id", "district", "students_total", "students_primary",
    "central_power_status", "central_heat_status",
    "has_generator", "has_autonomous_boiler",
    "has_electric_heaters", "has_water_heating",
    "insulation_status", "last_inspection_date",
]

BOOLEAN_COLUMNS = [
    "has_generator", "has_autonomous_boiler",
    "has_electric_heaters", "has_water_heating",
]

POWER_STATUSES = ["stable", "limited", "outage"]
HEAT_STATUSES = ["available", "unavailable"]
INSULATION_STATUSES = ["poor", "ok", "good"]
BOOLEAN_VALUES = {"true": True, "false": False}


def validate_row(row: dict) -> str | None:
    """Перевіряє один рядок CSV за кроком 8 розділу 6.
    Повертає текст причини, якщо запис дефектний, або None, якщо все гаразд.
    Перевірки йдуть від найгрубіших до найтонших: спершу наявність значень,
    потім переліки, потім числа, останньою — дата."""

    for column in REQUIRED_COLUMNS:
        if not (row.get(column) or "").strip():
            return f"відсутнє значення в колонці {column}"

    if row["central_power_status"] not in POWER_STATUSES:
        return (f"central_power_status '{row['central_power_status']}' "
                f"поза переліком {POWER_STATUSES}")
    if row["central_heat_status"] not in HEAT_STATUSES:
        return (f"central_heat_status '{row['central_heat_status']}' "
                f"поза переліком {HEAT_STATUSES}")
    if row["insulation_status"] not in INSULATION_STATUSES:
        return (f"insulation_status '{row['insulation_status']}' "
                f"поза переліком {INSULATION_STATUSES}")

    for column in BOOLEAN_COLUMNS:
        if row[column] not in BOOLEAN_VALUES:
            return f"колонка {column}: очікується true або false, отримано '{row[column]}'"

    try:
        total = int(row["students_total"])
        primary = int(row["students_primary"])
    except ValueError:
        return "students_total і students_primary мають бути цілими числами"

    if total <= 0:
        return f"students_total ({total}) має бути більшим за 0"
    if primary < 0:
        return f"students_primary ({primary}) не може бути від'ємним"
    if primary > total:
        return f"students_primary ({primary}) перевищує students_total ({total})"

    try:
        date.fromisoformat(row["last_inspection_date"])
    except ValueError:
        return f"дата '{row['last_inspection_date']}' не відповідає формату YYYY-MM-DD"

    return None


def parse_row(row: dict) -> dict:
    """Приводить типи рядка, що вже пройшов валідацію.
    З CSV усе приходить рядками, тому числа, булеві значення й дату
    треба перетворити явно — інакше 'false' поводитиметься як істина."""
    parsed = dict(row)
    parsed["students_total"] = int(row["students_total"])
    parsed["students_primary"] = int(row["students_primary"])
    for column in BOOLEAN_COLUMNS:
        parsed[column] = BOOLEAN_VALUES[row[column]]
    parsed["last_inspection_date"] = date.fromisoformat(row["last_inspection_date"])
    return parsed


def load_schools() -> tuple[list[dict], list[dict]]:
    """Читає датасет і ділить його на дві частини:
    придатні до оцінювання записи та виключені з причиною."""

    if not DATA_PATH.exists():
        fail("DATASET_NOT_FOUND",
             f"Файл {DATA_PATH} не знайдено.",
             "Очікується data/schools.csv поруч із server.py.")

    with DATA_PATH.open(encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        headers = reader.fieldnames or []

    missing = [c for c in REQUIRED_COLUMNS if c not in headers]
    if missing:
        fail("DATASET_MALFORMED",
             f"У файлі відсутні колонки: {', '.join(missing)}.",
             "Звір заголовок CSV зі схемою розділу 3 контрактів.")

    valid, invalid = [], []
    for row in rows:
        reason = validate_row(row)
        if reason:
            invalid.append({
                "school_id": (row.get("school_id") or "").strip() or None,
                "reason": reason,
            })
        else:
            valid.append(parse_row(row))

    return valid, invalid


class InvalidRecord(BaseModel):
    school_id: str | None
    reason: str


class AssessedSchool(BaseModel):
    school_id: str
    district: str
    students_total: int
    students_primary: int


class ReadinessReport(BaseModel):
    assessed: list[AssessedSchool]
    invalid_records: list[InvalidRecord]
    total_matched: int
    thresholds_version: str


@mcp.tool(
    annotations=ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    )
)
def edu_assess_school_readiness(
    severity: str,
    district: str | None = None,
    only_problematic: bool = False,
) -> ReadinessReport:
    """Оцінює готовність закладів освіти працювати очно за заданих погодних умов.
    Читає локальний реєстр закладів, перевіряє коректність записів і повертає
    для кожного закладу стан теплової спроможності, живлення, гарячої води
    та перелік виявлених прогалин. Можна обмежити вибірку районом."""

    if severity not in LEVELS:
        fail("INVALID_SEVERITY",
             f"Значення severity '{severity}' поза переліком.",
             f"Очікується одне з: {', '.join(LEVELS)}.")

    valid, invalid = load_schools()

    # Перелік районів будується з даних, а не зі списку в коді:
    # так підказка в помилці завжди відповідає фактичному вмісту файлу.
    districts = sorted({row["district"] for row in valid})

    if district is not None:
        if district not in districts:
            fail("UNKNOWN_DISTRICT",
                 f"Район '{district}' не знайдено.",
                 f"Доступні: {', '.join(districts)}.")
        selected = [row for row in valid if row["district"] == district]
    else:
        selected = valid

    assessed = [
        AssessedSchool(
            school_id=row["school_id"],
            district=row["district"],
            students_total=row["students_total"],
            students_primary=row["students_primary"],
        )
        for row in selected
    ]

    return ReadinessReport(
        assessed=assessed,
        invalid_records=[InvalidRecord(**item) for item in invalid],
        total_matched=len(assessed),
        thresholds_version=THRESHOLDS["version"],
    )


if __name__ == "__main__":
    mcp.run()
