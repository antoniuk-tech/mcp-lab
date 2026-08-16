"""Генерує синтетичний реєстр закладів освіти.

Дані синтетичні. Структура відповідає предметній області,
значення згенеровані з фіксованим seed для відтворюваності.
"""
import csv
import random
from datetime import date, timedelta

SEED = 42
TODAY = date(2026, 11, 15)
OUT = "data/schools.csv"

DISTRICTS = [
    "Голосіївський", "Дарницький", "Деснянський", "Дніпровський",
    "Оболонський", "Печерський", "Подільський", "Святошинський",
    "Солом'янський", "Шевченківський",
]

POWER = ["stable", "limited", "outage"]
HEAT = ["available", "unavailable"]
INSULATION = ["poor", "ok", "good"]


def make_school(idx, district, power, heat, rnd):
    total = rnd.randrange(300, 1200, 20)
    primary = int(total * rnd.uniform(0.28, 0.42))
    return {
        "school_id": f"SCH-{idx:03d}",
        "district": district,
        "students_total": total,
        "students_primary": primary,
        "central_power_status": power,
        "central_heat_status": heat,
        "has_generator": rnd.random() < 0.35,
        "has_autonomous_boiler": rnd.random() < 0.20,
        "has_electric_heaters": rnd.random() < 0.55,
        "has_water_heating": rnd.random() < 0.60,
        "insulation_status": rnd.choice(INSULATION),
        "last_inspection_date": (TODAY - timedelta(days=rnd.randrange(20, 500))).isoformat(),
    }


def main():
    rnd = random.Random(SEED)
    rows = []
    idx = 1

    # Блок покриття: усі 6 комбінацій станів постачання гарантовано присутні
    for power in POWER:
        for heat in HEAT:
            rows.append(make_school(idx, DISTRICTS[(idx - 1) % 10], power, heat, rnd))
            idx += 1

    # Решта — випадкові
    while idx <= 40:
        rows.append(make_school(
            idx, DISTRICTS[(idx - 1) % 10],
            rnd.choices(POWER, weights=[5, 3, 2])[0],
            rnd.choices(HEAT, weights=[7, 3])[0],
            rnd,
        ))
        idx += 1

    # Один навмисно дефектний запис для демонстрації валідації
    broken = make_school(41, DISTRICTS[0], "stable", "available", rnd)
    broken["students_primary"] = broken["students_total"] + 180
    rows.append(broken)

    with open(OUT, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        for row in rows:
            row = dict(row)
            for key in ("has_generator", "has_autonomous_boiler",
                        "has_electric_heaters", "has_water_heating"):
                row[key] = "true" if row[key] else "false"
            writer.writerow(row)

    print(f"Записано {len(rows)} рядків у {OUT}")


if __name__ == "__main__":
    main()
