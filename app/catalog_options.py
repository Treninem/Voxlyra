from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Choice:
    code: str
    label: str


BOOK_TYPES = [
    Choice("novel", "Роман"),
    Choice("serial", "Серия / цикл"),
    Choice("story", "Повесть"),
    Choice("shorts", "Сборник рассказов"),
    Choice("poetry", "Стихи / поэзия"),
    Choice("fanfic", "Фанфик"),
    Choice("translation", "Перевод"),
    Choice("manga", "Манга / манхва / комикс"),
    Choice("audio", "Аудиокнига"),
    Choice("mixed", "Текст + аудио"),
]

LANGUAGES = [
    Choice("ru", "Русский"),
    Choice("en", "Английский"),
    Choice("zh", "Китайский"),
    Choice("ja", "Японский"),
    Choice("ko", "Корейский"),
    Choice("es", "Испанский"),
    Choice("de", "Немецкий"),
    Choice("fr", "Французский"),
    Choice("other", "Другой язык"),
]

GENRES = [
    Choice("fantasy", "Фэнтези"), Choice("dark_fantasy", "Тёмное фэнтези"), Choice("epic_fantasy", "Эпическое фэнтези"),
    Choice("urban_fantasy", "Городское фэнтези"), Choice("slavic_fantasy", "Славянское фэнтези"), Choice("asian_fantasy", "Азиатское фэнтези"),
    Choice("lit_rpg", "ЛитРПГ"), Choice("rpg", "Игровое фэнтези"), Choice("cultivation", "Культивация / сянься"),
    Choice("wuxia", "Уся"), Choice("xuanhuan", "Сюаньхуань"), Choice("isekai", "Исекай"),
    Choice("portal", "Попаданцы"), Choice("reincarnation", "Перерождение"), Choice("progression", "Прогрессия силы"),
    Choice("magic_academy", "Магическая академия"), Choice("academy", "Академия"), Choice("supernatural", "Сверхъестественное"),
    Choice("sci_fi", "Фантастика"), Choice("space", "Космоопера"), Choice("cyberpunk", "Киберпанк"),
    Choice("postapoc", "Постапокалипсис"), Choice("dystopia", "Антиутопия"), Choice("steampunk", "Стимпанк"),
    Choice("biopunk", "Биопанк"), Choice("solarpunk", "Соларпанк"), Choice("time_travel", "Путешествия во времени"),
    Choice("alternate_history", "Альтернативная история"), Choice("ai", "Искусственный интеллект"), Choice("mecha", "Меха"),
    Choice("romance", "Романтика"), Choice("love_story", "Любовный роман"), Choice("drama", "Драма"),
    Choice("melodrama", "Мелодрама"), Choice("romantasy", "Романтическое фэнтези"), Choice("historical_romance", "Исторический роман"),
    Choice("comedy", "Комедия"), Choice("slice", "Повседневность"), Choice("family", "Семейная сага"),
    Choice("detective", "Детектив"), Choice("thriller", "Триллер"), Choice("mystery", "Мистика"),
    Choice("horror", "Ужасы"), Choice("dark_horror", "Тёмный хоррор"), Choice("gothic_horror", "Готический хоррор"),
    Choice("cosmic_horror", "Космический хоррор"), Choice("paranormal_horror", "Паранормальный хоррор"), Choice("psychological_horror", "Психологический хоррор"),
    Choice("psychological", "Психология"), Choice("crime", "Криминал"), Choice("noir", "Нуар"),
    Choice("police", "Полицейский детектив"), Choice("cozy_mystery", "Уютный детектив"), Choice("courtroom", "Судебная драма"),
    Choice("action", "Боевик"), Choice("adventure", "Приключения"), Choice("war", "Военное"),
    Choice("martial", "Боевые искусства"), Choice("survival", "Выживание"), Choice("western", "Вестерн"),
    Choice("historical", "Историческое"), Choice("mythology", "Мифология"), Choice("fairytale", "Сказка"),
    Choice("young_adult", "Young Adult"), Choice("new_adult", "New Adult"), Choice("teen", "Подростковое"), Choice("children", "Детское"),
    Choice("school_life", "Школьная жизнь"), Choice("college", "Студенческая жизнь"), Choice("slice_healing", "Уютное / исцеляющее"),
    Choice("nonfiction", "Нон-фикшн"), Choice("self_dev", "Саморазвитие"), Choice("business", "Бизнес"),
    Choice("popular_science", "Научпоп"), Choice("biography", "Биография"), Choice("memoir", "Мемуары"),
    Choice("humor", "Юмор"), Choice("satire", "Сатира"), Choice("parody", "Пародия"),
    Choice("lgbtq_romance", "ЛГБТК+ романтика"), Choice("erotic", "Эротика 18+"), Choice("adult_dark", "Мрачное 18+"),
    Choice("manhwa", "Манхва"), Choice("manhua", "Маньхуа"), Choice("manga_jp", "Манга"),
    Choice("webnovel", "Веб-новелла"), Choice("light_novel", "Лайт-новелла"), Choice("ranobe", "Ранобэ"),
    Choice("body_horror", "Боди-хоррор"), Choice("folk_horror", "Фолк-хоррор"), Choice("surreal", "Сюрреализм"),
    Choice("dark_romance", "Тёмная романтика"), Choice("sports_romance", "Спортивная романтика"), Choice("office_romance", "Офисная романтика"),
    Choice("medical_drama", "Медицинская драма"), Choice("legal_thriller", "Юридический триллер"), Choice("political_thriller", "Политический триллер"),
    Choice("spy", "Шпионский роман"), Choice("techno_thriller", "Технотриллер"), Choice("military_sci_fi", "Военная фантастика"),
    Choice("hard_sci_fi", "Твёрдая НФ"), Choice("soft_sci_fi", "Социальная фантастика"), Choice("cli_fi", "Климатическая фантастика"),
    Choice("cozy_fantasy", "Уютное фэнтези"), Choice("grimdark", "Гримдарк"), Choice("low_fantasy", "Низкое фэнтези"),
    Choice("high_fantasy", "Высокое фэнтези"), Choice("historical_fantasy", "Историческое фэнтези"), Choice("religious_mystery", "Религиозная мистика"),
]

TROPES = [
    Choice("strong_mc", "Сильный герой"), Choice("weak_to_strong", "От слабого к сильному"), Choice("antihero", "Антигерой"),
    Choice("villain_mc", "Герой-злодей"), Choice("smart_mc", "Умный герой"), Choice("overpowered", "Имба / сверхсила"),
    Choice("system", "Система"), Choice("levels", "Уровни и ранги"), Choice("skills", "Навыки"),
    Choice("necromancer", "Некромант"), Choice("summoner", "Призыватель"), Choice("mage", "Маг"),
    Choice("assassin", "Ассасин"), Choice("healer", "Целитель"), Choice("beast_tamer", "Укротитель зверей"),
    Choice("dungeon", "Подземелья"), Choice("tower", "Башня испытаний"), Choice("guilds", "Гильдии"),
    Choice("clans", "Кланы"), Choice("sects", "Секты"), Choice("kingdom", "Королевства"),
    Choice("empire", "Империя"), Choice("nobility", "Аристократия"), Choice("politics", "Интриги и политика"),
    Choice("revenge", "Месть"), Choice("redemption", "Искупление"), Choice("chosen_one", "Избранный"),
    Choice("hidden_power", "Скрытая сила"), Choice("secret_identity", "Тайная личность"), Choice("academy_arc", "Арка академии"),
    Choice("tournament", "Турнир"), Choice("training", "Тренировки"), Choice("crafting", "Крафт / ремёсла"),
    Choice("alchemy", "Алхимия"), Choice("artifacts", "Артефакты"), Choice("pets", "Питомцы / фамильяры"),
    Choice("dragons", "Драконы"), Choice("demons", "Демоны"), Choice("angels", "Ангелы"),
    Choice("undead", "Нежить"), Choice("vampires", "Вампиры"), Choice("werewolves", "Оборотни"),
    Choice("gods", "Боги"), Choice("ancient_ruins", "Древние руины"), Choice("sealed_evil", "Запечатанное зло"),
    Choice("romance_slow", "Медленная романтика"), Choice("love_triangle", "Любовный треугольник"), Choice("enemies_lovers", "От врагов к любви"),
    Choice("friends_lovers", "От друзей к любви"), Choice("fake_relationship", "Фальшивые отношения"), Choice("found_family", "Найденная семья"),
    Choice("harem", "Гарем"), Choice("reverse_harem", "Обратный гарем"), Choice("no_romance", "Без романтики"),
    Choice("detective_case", "Расследование"), Choice("murder_mystery", "Убийство и тайна"), Choice("psychological_games", "Психологические игры"),
    Choice("survival_game", "Игра на выживание"), Choice("zombies", "Зомби"), Choice("monsters", "Монстры"),
    Choice("ghosts", "Призраки"), Choice("curses", "Проклятия"), Choice("haunted_house", "Проклятый дом"),
    Choice("serial_killer", "Маньяк / серийный убийца"), Choice("conspiracy", "Заговор"), Choice("secret_society", "Тайное общество"),
    Choice("military", "Армия"), Choice("strategy", "Стратегия"), Choice("base_building", "Строительство базы"),
    Choice("business_building", "Развитие бизнеса"), Choice("kingdom_building", "Развитие королевства"), Choice("farm", "Ферма / уютное развитие"),
    Choice("cooking", "Кулинария"), Choice("medicine", "Медицина"), Choice("sports", "Спорт"),
    Choice("music", "Музыка"), Choice("showbiz", "Шоу-бизнес"), Choice("school", "Школа"),
    Choice("workplace", "Работа / офис"), Choice("road", "Путешествие"), Choice("sea", "Море / пираты"),
    Choice("slow_burn_plot", "Медленное раскрытие сюжета"), Choice("plot_twists", "Повороты сюжета"), Choice("cliffhangers", "Клиффхэнгеры"),
    Choice("multiple_pov", "Несколько точек зрения"), Choice("unreliable_narrator", "Ненадёжный рассказчик"), Choice("dark_secret", "Тёмная тайна"),
    Choice("contract_marriage", "Контрактные отношения"), Choice("second_chance", "Второй шанс"), Choice("mentor_student", "Наставник и ученик"),
    Choice("rivals", "Соперники"), Choice("forced_proximity", "Вынужденная близость"), Choice("found_footage", "Найденные записи"),
    Choice("locked_room", "Запертая комната"), Choice("cold_case", "Старое дело"), Choice("heist", "Ограбление"),
    Choice("court_intrigue", "Дворцовые интриги"), Choice("merchant", "Торговец / караван"), Choice("naval", "Флот и корабли"),
    Choice("robot_companion", "Робот-компаньон"), Choice("alien_contact", "Первый контакт"), Choice("virtual_world", "Виртуальный мир"),
]

AUDIENCES = [
    Choice("all", "Для широкой аудитории"), Choice("male", "Больше для мужской аудитории"), Choice("female", "Больше для женской аудитории"),
    Choice("teen", "Подростки"), Choice("young", "18–25"), Choice("adult", "25+"), Choice("mature", "Зрелая аудитория"),
    Choice("casual", "Лёгкое чтение"), Choice("deep", "Любителям глубокого сюжета"), Choice("fast", "Любителям динамики"),
    Choice("slow", "Любителям медленного развития"), Choice("romance_fans", "Любителям романтики"), Choice("action_fans", "Любителям экшена"),
    Choice("audio_fans", "Тем, кто слушает аудио"), Choice("series_fans", "Любителям длинных циклов"),
    Choice("manga_fans", "Любителям манги/манхвы"), Choice("premium", "Готовым покупать главы"), Choice("free_first", "Ищущим бесплатный старт"),
    Choice("night_readers", "Ночное чтение"), Choice("commute_audio", "Аудио в дороге"), Choice("short_sessions", "Короткие сессии"),
]

CONTENT_WARNINGS = [
    Choice("none", "Без особых предупреждений"), Choice("violence", "Насилие"), Choice("blood", "Кровь"),
    Choice("death", "Смерть персонажей"), Choice("war", "Война"), Choice("trauma", "Травмирующие темы"),
    Choice("dark", "Мрачная атмосфера"), Choice("horror", "Пугающие сцены"), Choice("abuse", "Жестокое обращение"),
    Choice("language", "Грубая лексика"), Choice("adult", "Интимные сцены 18+"), Choice("drugs", "Упоминание веществ"),
    Choice("gambling", "Азартные игры"), Choice("selfharm", "Самоповреждение"), Choice("suicide", "Суицидальные темы"),
    Choice("spoilers", "Спойлерные предупреждения"), Choice("body_horror", "Телесный хоррор"),
    Choice("psych_pressure", "Психологическое давление"), Choice("toxic_relationship", "Токсичные отношения"),
    Choice("religion", "Религиозные темы"), Choice("politics", "Политические темы"), Choice("medical", "Медицинские сцены"),
]


AD_PLACEMENTS = [
    Choice("reader_top", "Сверху главы"),
    Choice("reader_bottom", "Снизу главы"),
    Choice("reader_both", "Сверху и снизу главы"),
    Choice("catalog_featured", "Витрина каталога"),
    Choice("audio_featured", "Витрина аудио"),
]

PROMO_DISCOUNTS = [
    Choice("10", "Скидка 10%"),
    Choice("25", "Скидка 25%"),
    Choice("50", "Скидка 50%"),
    Choice("100", "Бесплатный доступ"),
]


# Дополнение этапа 9: расширенные категории, чтобы автор чаще выбирал галочками, а не писал вручную.
GENRES += [
    Choice("cozy_detective", "Уютный детектив"), Choice("dark_academia", "Тёмная академия"),
    Choice("magical_realism", "Магический реализм"), Choice("weird_fiction", "Странная проза"),
    Choice("psychological_thriller", "Психологический триллер"), Choice("domestic_thriller", "Бытовой триллер"),
    Choice("survival_horror", "Хоррор на выживание"), Choice("slasher", "Слэшер"),
    Choice("monster_horror", "Монстр-хоррор"), Choice("occult", "Оккультизм"),
    Choice("dark_detective", "Мрачный детектив"), Choice("historical_detective", "Исторический детектив"),
    Choice("procedural", "Процедурал"), Choice("romantic_suspense", "Романтический саспенс"),
    Choice("space_horror", "Космический хоррор"), Choice("superhero", "Супергероика"),
    Choice("monster_romance", "Монстр-романтика"), Choice("omegaverse", "Омегаверс"),
    Choice("boys_love", "BL"), Choice("girls_love", "GL"),
    Choice("villainess", "Злодейка"), Choice("regression", "Регрессия"),
    Choice("transmigration", "Переселение души"), Choice("danmei", "Даньмэй"),
    Choice("quick_transmigration", "Быстрая трансмиграция"), Choice("apocalypse_system", "Апокалипсис с системой"),
    Choice("cozy_mystic", "Уютная мистика"), Choice("fairy_retelling", "Переосмысление сказок"),
    Choice("myth_retelling", "Переосмысление мифов"), Choice("literary_fiction", "Литературная проза"),
    Choice("experimental", "Экспериментальная проза"), Choice("audio_drama", "Аудиоспектакль"),
]

TROPES += [
    Choice("fake_villain", "Ложный злодей"), Choice("morally_gray", "Морально серые герои"),
    Choice("academy_survival", "Выживание в академии"), Choice("teacher_mc", "Герой-наставник"),
    Choice("female_lead", "Главная героиня"), Choice("male_lead", "Главный герой"),
    Choice("dual_leads", "Два главных героя"), Choice("ensemble_cast", "Ансамбль персонажей"),
    Choice("cozy_mystery_case", "Уютное расследование"), Choice("dark_investigation", "Мрачное расследование"),
    Choice("forbidden_magic", "Запретная магия"), Choice("bloodline", "Родословная/кровь"),
    Choice("ancient_god", "Древний бог"), Choice("evil_cult", "Зловещий культ"),
    Choice("space_station", "Космическая станция"), Choice("lost_colony", "Потерянная колония"),
    Choice("demon_contract", "Договор с демоном"), Choice("arranged_marriage", "Договорной брак"),
    Choice("time_loop", "Временная петля"), Choice("memory_loss", "Потеря памяти"),
    Choice("body_swap", "Обмен телами"), Choice("secret_child", "Тайный ребёнок"),
    Choice("inheritance", "Наследство"), Choice("small_town", "Маленький город"),
    Choice("isolated_place", "Изолированное место"), Choice("snowed_in", "Заперты снегом"),
]

AUDIENCES += [
    Choice("horror_fans", "Любителям хоррора"), Choice("detective_fans", "Любителям детектива"),
    Choice("dark_fantasy_fans", "Любителям тёмного фэнтези"), Choice("cultivation_fans", "Любителям культивации"),
    Choice("romantasy_fans", "Любителям романтического фэнтези"), Choice("short_audio", "Короткое аудио"),
    Choice("long_audio", "Длинное аудио"), Choice("completed_only", "Только завершённые истории"),
]

CONTENT_WARNINGS += [
    Choice("animal_death", "Гибель животных"), Choice("kidnapping", "Похищение"),
    Choice("stalking", "Преследование"), Choice("manipulation", "Манипуляции"),
    Choice("claustrophobia", "Клаустрофобия"), Choice("insects", "Насекомые"),
    Choice("pregnancy", "Беременность"), Choice("major_spoiler_theme", "Сильные спойлерные темы"),
]

GROUPS = {
    "type": ("book_type", BOOK_TYPES),
    "lang": ("language", LANGUAGES),
    "g": ("genres", GENRES),
    "t": ("tropes", TROPES),
    "a": ("audience", AUDIENCES),
    "c": ("warnings", CONTENT_WARNINGS),
}

DB_GROUP_TO_CHOICES = {db_group: choices for _, (db_group, choices) in GROUPS.items()}
DB_GROUP_TO_CHOICES["adplace"] = AD_PLACEMENTS
DB_GROUP_TO_CHOICES["discount"] = PROMO_DISCOUNTS


def choices_for_prefix(prefix: str) -> list[Choice]:
    return GROUPS[prefix][1]


def db_group_for_prefix(prefix: str) -> str:
    return GROUPS[prefix][0]


def label_for(prefix_or_group: str, code: str) -> str:
    if prefix_or_group in GROUPS:
        choices = GROUPS[prefix_or_group][1]
    else:
        choices = DB_GROUP_TO_CHOICES.get(prefix_or_group, [])
    for choice in choices:
        if choice.code == code:
            return choice.label
    return code


def labels_for(prefix_or_group: str, codes: list[str] | set[str] | tuple[str, ...]) -> list[str]:
    return [label_for(prefix_or_group, code) for code in codes]
