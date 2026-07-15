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
    Choice("cozy_detective", "Лёгкий уютный детектив"), Choice("dark_academia", "Тёмная академия"),
    Choice("magical_realism", "Магический реализм"), Choice("weird_fiction", "Странная проза"),
    Choice("psychological_thriller", "Психологический триллер"), Choice("domestic_thriller", "Бытовой триллер"),
    Choice("survival_horror", "Хоррор на выживание"), Choice("slasher", "Слэшер"),
    Choice("monster_horror", "Монстр-хоррор"), Choice("occult", "Оккультизм"),
    Choice("dark_detective", "Мрачный детектив"), Choice("historical_detective", "Исторический детектив"),
    Choice("procedural", "Процедурал"), Choice("romantic_suspense", "Романтический саспенс"),
    Choice("space_horror", "Хоррор в космосе"), Choice("superhero", "Супергероика"),
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


# VoxLyra v1.11.9: полный, но управляемый классификатор.
# Тип описывает форму произведения, жанр — содержание, сюжетные теги — особенности,
# предупреждения — чувствительные темы. Списки не скрывают редкие варианты: интерфейс
# сначала показывает подходящее, затем позволяет перейти в тематический раздел или ко всем пунктам.
BOOK_TYPES += [
    Choice("web_serial", "Веб-роман / сериал"),
    Choice("novella", "Новелла"),
    Choice("short_story", "Рассказ"),
    Choice("anthology", "Антология"),
    Choice("light_novel_type", "Лайт-новелла / ранобэ"),
    Choice("interactive", "Интерактивная книга / книга-игра"),
    Choice("diary", "Дневник / записки"),
    Choice("epistolary", "Письма / переписка"),
    Choice("play", "Пьеса"),
    Choice("screenplay", "Сценарий"),
    Choice("essay", "Эссе / очерки"),
    Choice("nonfiction_book", "Нон-фикшн"),
    Choice("guide", "Практическое руководство"),
    Choice("reference", "Справочник / энциклопедия"),
    Choice("biography_type", "Биография / мемуары"),
    Choice("educational", "Учебное издание"),
    Choice("children_picture", "Иллюстрированная детская книга"),
    Choice("audio_play", "Аудиоспектакль"),
]

GENRES += [
    Choice("contemporary_fiction", "Современная проза"),
    Choice("social_fiction", "Социальная проза"),
    Choice("philosophical_fiction", "Философская проза"),
    Choice("domestic_fiction", "Бытовая проза"),
    Choice("coming_of_age", "Взросление"),
    Choice("workplace_fiction", "Производственный роман"),
    Choice("village_prose", "Деревенская проза"),
    Choice("immigrant_fiction", "Проза о переезде и эмиграции"),
    Choice("family_drama", "Семейная драма"),
    Choice("tragicomedy", "Трагикомедия"),
    Choice("absurdism", "Абсурдизм"),
    Choice("contemporary_romance", "Современный любовный роман"),
    Choice("paranormal_romance", "Паранормальная романтика"),
    Choice("military_romance", "Военная романтика"),
    Choice("royal_romance", "Королевская романтика"),
    Choice("holiday_romance", "Праздничная романтика"),
    Choice("regency_romance", "Романтика эпохи Регентства"),
    Choice("western_romance", "Романтический вестерн"),
    Choice("romantic_comedy", "Романтическая комедия"),
    Choice("forbidden_romance", "Запретная любовь"),
    Choice("sword_sorcery", "Меч и магия"),
    Choice("mythic_fantasy", "Мифологическое фэнтези"),
    Choice("gaslamp_fantasy", "Газламп-фэнтези"),
    Choice("flintlock_fantasy", "Пороховое фэнтези"),
    Choice("arcanepunk", "Арканопанк"),
    Choice("dungeon_core", "Данжн-кор"),
    Choice("gamelit", "Геймлит"),
    Choice("progression_fantasy", "Прогрессорское фэнтези"),
    Choice("speculative_fiction", "Спекулятивная фантастика"),
    Choice("near_future", "Ближнее будущее"),
    Choice("first_contact", "Первый контакт"),
    Choice("alien_invasion", "Вторжение пришельцев"),
    Choice("planetary_romance", "Планетарная фантастика"),
    Choice("space_western", "Космический вестерн"),
    Choice("generation_ship", "Корабль поколений"),
    Choice("utopia", "Утопия"),
    Choice("hardboiled", "Крутой детектив"),
    Choice("whodunit", "Классический детектив-загадка"),
    Choice("amateur_detective", "Любительский детектив"),
    Choice("crime_drama", "Криминальная драма"),
    Choice("caper", "Авантюрное ограбление"),
    Choice("espionage", "Шпионская проза"),
    Choice("analog_horror", "Аналоговый хоррор"),
    Choice("tech_horror", "Технологический хоррор"),
    Choice("occult_horror", "Оккультный хоррор"),
    Choice("possession_horror", "Хоррор об одержимости"),
    Choice("ancient_fiction", "Античная историческая проза"),
    Choice("medieval_fiction", "Средневековая историческая проза"),
    Choice("regency_fiction", "Проза эпохи Регентства"),
    Choice("victorian_fiction", "Викторианская проза"),
    Choice("twentieth_century_fiction", "Историческая проза XX века"),
    Choice("historical_adventure", "Исторические приключения"),
    Choice("pirate_adventure", "Пиратские приключения"),
    Choice("treasure_hunt", "Поиск сокровищ"),
    Choice("expedition", "Экспедиция"),
    Choice("disaster", "Катастрофа"),
    Choice("sea_adventure", "Морские приключения"),
    Choice("middle_grade", "Детская проза 9–12 лет"),
    Choice("picture_book", "Книга-картинка"),
    Choice("bedtime_stories", "Сказки на ночь"),
    Choice("educational_children", "Познавательное детское"),
    Choice("animal_stories", "Истории о животных"),
    Choice("history_nonfiction", "История / документалистика"),
    Choice("psychology_nonfiction", "Психология и отношения"),
    Choice("philosophy_nonfiction", "Философия"),
    Choice("religion_nonfiction", "Религия и духовность"),
    Choice("economics", "Экономика"),
    Choice("finance", "Финансы"),
    Choice("career", "Карьера и профессии"),
    Choice("parenting", "Родительство"),
    Choice("health", "Здоровье"),
    Choice("medicine_nonfiction", "Медицина / научпоп"),
    Choice("travelogue", "Путевые заметки"),
    Choice("cooking_nonfiction", "Кулинария"),
    Choice("technology", "Технологии"),
    Choice("programming", "Программирование"),
    Choice("art_nonfiction", "Искусство"),
    Choice("music_nonfiction", "Музыка"),
    Choice("cinema_nonfiction", "Кино и театр"),
    Choice("journalism", "Журналистика / репортаж"),
    Choice("essays", "Эссеистика"),
    Choice("documentary", "Документальная проза"),
    Choice("true_story", "Реальная история"),
    Choice("education", "Образование"),
    Choice("reference_nonfiction", "Справочная литература"),
    Choice("true_crime", "Документальный криминал"),
    Choice("lyric_poetry", "Лирическая поэзия"),
    Choice("narrative_poetry", "Сюжетная поэзия"),
    Choice("prose_poetry", "Поэзия в прозе"),
    Choice("song_lyrics", "Тексты песен"),
]

TROPES += [
    Choice("underdog", "Недооценённый герой"),
    Choice("reluctant_hero", "Герой поневоле"),
    Choice("fallen_hero", "Падший герой"),
    Choice("prodigy", "Юный гений"),
    Choice("fish_out_of_water", "Чужак в новом мире"),
    Choice("double_life", "Двойная жизнь"),
    Choice("identity_reveal", "Раскрытие личности"),
    Choice("prophecy", "Пророчество"),
    Choice("quest", "Большое путешествие / квест"),
    Choice("rescue_mission", "Спасательная миссия"),
    Choice("escape", "Побег"),
    Choice("chase", "Погоня"),
    Choice("rebellion", "Восстание"),
    Choice("revolution", "Революция"),
    Choice("civil_war", "Гражданская война"),
    Choice("apocalypse", "Апокалипсис"),
    Choice("catastrophe", "Катастрофа"),
    Choice("colonization", "Освоение новых земель / планет"),
    Choice("parallel_worlds", "Параллельные миры"),
    Choice("multiverse", "Мультивселенная"),
    Choice("simulation", "Мир-симуляция"),
    Choice("reincarnated_villain", "Перерождение в злодея"),
    Choice("system_apocalypse", "Система после апокалипсиса"),
    Choice("dungeon_core_mc", "Герой — ядро подземелья"),
    Choice("cultivation_path", "Путь культивации"),
    Choice("regression_to_past", "Возврат в прошлое"),
    Choice("transmigration_world", "Переселение в другой мир"),
    Choice("summoned_hero", "Призванный герой"),
    Choice("magic_contract", "Магический договор"),
    Choice("cursed_object", "Проклятый предмет"),
    Choice("magical_school", "Магическая школа"),
    Choice("forbidden_love", "Запретная любовь"),
    Choice("grumpy_sunshine", "Угрюмый и солнечный"),
    Choice("opposites_attract", "Противоположности притягиваются"),
    Choice("one_bed", "Одна кровать"),
    Choice("roommates", "Соседи по дому"),
    Choice("bodyguard_romance", "Телохранитель и подопечный"),
    Choice("royal_commoner", "Монарх и простолюдин"),
    Choice("single_parent", "Одинокий родитель"),
    Choice("secret_romance", "Тайные отношения"),
    Choice("marriage_of_convenience", "Брак по расчёту"),
    Choice("workplace_romance", "Служебный роман"),
    Choice("holiday_romance", "Праздничная история любви"),
    Choice("siblings", "Братья и сёстры"),
    Choice("parent_child", "Родители и дети"),
    Choice("adoption", "Усыновление / опека"),
    Choice("family_secret", "Семейная тайна"),
    Choice("dynasty", "Семейная династия"),
    Choice("missing_person", "Исчезновение человека"),
    Choice("closed_circle", "Замкнутый круг подозреваемых"),
    Choice("occult_investigation", "Оккультное расследование"),
    Choice("possession", "Одержимость"),
    Choice("ritual", "Опасный ритуал"),
    Choice("cosmic_threat", "Космическая угроза"),
    Choice("slasher_chase", "Преследование убийцей"),
    Choice("urban_legend", "Городская легенда"),
    Choice("creature_feature", "Охота чудовища"),
    Choice("first_contact_trope", "Контакт с иной цивилизацией"),
    Choice("generation_ship_trope", "Жизнь на корабле поколений"),
    Choice("terraforming", "Терраформирование"),
    Choice("android_mc", "Андроид / синтетический герой"),
    Choice("clone", "Клоны"),
    Choice("genetic_engineering", "Генная инженерия"),
    Choice("ai_uprising", "Восстание ИИ"),
    Choice("posthuman", "Постчеловек"),
    Choice("cybercrime", "Киберпреступление"),
    Choice("virtual_reality", "Виртуальная реальность"),
    Choice("sports_team", "Спортивная команда"),
    Choice("competition", "Соревнование"),
    Choice("career_growth", "Карьерный рост"),
    Choice("startup", "Стартап"),
    Choice("restaurant", "Кафе / ресторан"),
    Choice("hospital", "Больница"),
    Choice("legal_case", "Судебное дело"),
    Choice("political_campaign", "Политическая кампания"),
    Choice("village_life", "Жизнь в деревне"),
    Choice("healing_journey", "Исцеление и принятие"),
    Choice("episodic", "Эпизодическая структура"),
    Choice("nonlinear", "Нелинейное повествование"),
    Choice("story_with_choices", "Выборы читателя влияют на сюжет"),
    Choice("multiple_endings", "Несколько концовок"),
    Choice("framed_story", "История в истории"),
    Choice("letters_diary", "Письма и дневники"),
    Choice("document_style", "Документальный стиль"),
    Choice("practical_steps", "Пошаговая практика"),
    Choice("exercises", "Задания и упражнения"),
    Choice("checklists", "Чек-листы"),
    Choice("case_studies", "Разборы случаев"),
    Choice("personal_experience", "Личный опыт автора"),
    Choice("interviews", "Интервью"),
    Choice("research_based", "Основано на исследованиях"),
    Choice("reference_format", "Справочный формат"),
    Choice("beginner_friendly", "Для начинающих"),
]

AUDIENCES += [
    Choice("kids_0_6", "Дети 0–6 лет"),
    Choice("kids_7_9", "Дети 7–9 лет"),
    Choice("kids_10_12", "Дети 10–12 лет"),
    Choice("teens_13_15", "Подростки 13–15 лет"),
    Choice("teens_16_17", "Подростки 16–17 лет"),
    Choice("adults_18_24", "Взрослые 18–24 года"),
    Choice("adults_25_34", "Взрослые 25–34 года"),
    Choice("adults_35_49", "Взрослые 35–49 лет"),
    Choice("adults_50_plus", "Взрослые 50+"),
    Choice("family_reading", "Семейное чтение"),
    Choice("beginner_readers", "Начинающим читателям жанра"),
    Choice("experienced_readers", "Опытным читателям жанра"),
    Choice("binge_readers", "Любителям читать запоем"),
    Choice("cozy_readers", "Для спокойного отдыха"),
    Choice("complex_world_fans", "Любителям сложных миров"),
    Choice("nonfiction_readers", "Читателям нон-фикшна"),
    Choice("professionals", "Профессиональной аудитории"),
    Choice("parents", "Родителям"),
    Choice("educators", "Педагогам"),
]

CONTENT_WARNINGS += [
    Choice("graphic_violence", "Подробные сцены насилия"),
    Choice("gore", "Жестокие подробности / расчленение"),
    Choice("torture", "Пытки"),
    Choice("domestic_violence", "Домашнее насилие"),
    Choice("child_abuse", "Жестокое обращение с детьми"),
    Choice("sexual_violence", "Сексуализированное насилие"),
    Choice("coercion", "Принуждение"),
    Choice("bullying", "Травля"),
    Choice("harassment", "Домогательства"),
    Choice("captivity", "Плен / заточение"),
    Choice("slavery", "Рабство"),
    Choice("child_death", "Смерть ребёнка"),
    Choice("parent_death", "Смерть родителя"),
    Choice("grief", "Горе и утрата"),
    Choice("depression", "Депрессивные состояния"),
    Choice("panic_attacks", "Панические атаки"),
    Choice("ptsd", "Посттравматические состояния"),
    Choice("eating_disorder", "Расстройства пищевого поведения"),
    Choice("addiction", "Зависимость"),
    Choice("mental_illness", "Психические расстройства"),
    Choice("alcohol", "Алкоголь"),
    Choice("smoking", "Курение"),
    Choice("drug_use", "Употребление наркотиков"),
    Choice("explicit_sex", "Подробные интимные сцены 18+"),
    Choice("sexual_content", "Сексуальные темы"),
    Choice("infidelity", "Измена"),
    Choice("age_gap", "Значительная разница в возрасте"),
    Choice("miscarriage", "Потеря беременности"),
    Choice("infertility", "Бесплодие"),
    Choice("childbirth", "Роды"),
    Choice("abortion", "Прерывание беременности"),
    Choice("serious_illness", "Тяжёлая болезнь"),
    Choice("cancer", "Онкологическое заболевание"),
    Choice("surgery", "Операции"),
    Choice("needles", "Иглы / уколы"),
    Choice("vomiting", "Рвота"),
    Choice("drowning", "Утопление"),
    Choice("fire", "Пожар / ожоги"),
    Choice("heights", "Высота"),
    Choice("spiders", "Пауки"),
    Choice("snakes", "Змеи"),
    Choice("animal_harm", "Жестокое обращение с животными"),
    Choice("discrimination", "Дискриминация"),
    Choice("racism", "Расизм"),
    Choice("xenophobia", "Ксенофобия"),
    Choice("homophobia", "Гомофобия / трансфобия"),
    Choice("religious_conflict", "Религиозный конфликт"),
    Choice("political_extremism", "Политический экстремизм"),
    Choice("terrorism", "Терроризм"),
    Choice("natural_disaster", "Стихийные бедствия"),
]


@dataclass(frozen=True)
class OptionSection:
    code: str
    label: str


OPTION_SECTIONS: dict[str, list[OptionSection]] = {
    "g": [
        OptionSection("recommended", "Подходящее"),
        OptionSection("fantasy_game", "Фэнтези и игры"),
        OptionSection("scifi", "Фантастика"),
        OptionSection("romance", "Романтика"),
        OptionSection("mystery_crime", "Детектив и криминал"),
        OptionSection("horror_mystic", "Хоррор и мистика"),
        OptionSection("contemporary_drama", "Современная проза"),
        OptionSection("historical_adventure", "История и приключения"),
        OptionSection("young_children", "Детское и подростковое"),
        OptionSection("nonfiction", "Нон-фикшн"),
        OptionSection("humor_poetry", "Юмор и поэзия"),
        OptionSection("asian_formats", "Азиатские форматы"),
        OptionSection("all", "Все по алфавиту"),
    ],
    "t": [
        OptionSection("recommended", "Подходящее"),
        OptionSection("hero", "Герои"),
        OptionSection("power_magic", "Силы и магия"),
        OptionSection("world_society", "Мир и общество"),
        OptionSection("quest_conflict", "Сюжет и конфликт"),
        OptionSection("romance", "Отношения и романтика"),
        OptionSection("family", "Семья и дружба"),
        OptionSection("mystery_crime", "Тайны и расследования"),
        OptionSection("horror", "Хоррор"),
        OptionSection("scifi", "Фантастика"),
        OptionSection("everyday", "Работа и повседневность"),
        OptionSection("structure", "Подача и структура"),
        OptionSection("nonfiction", "Нон-фикшн"),
        OptionSection("all", "Все по алфавиту"),
    ],
    "a": [
        OptionSection("recommended", "Подходящее"),
        OptionSection("age", "По возрасту"),
        OptionSection("interests", "По интересам"),
        OptionSection("reading_style", "По стилю чтения"),
        OptionSection("format", "По формату"),
        OptionSection("all", "Все по алфавиту"),
    ],
    "c": [
        OptionSection("recommended", "Возможные по выбранному"),
        OptionSection("violence", "Насилие и опасность"),
        OptionSection("mental", "Психологические темы"),
        OptionSection("relationships", "Отношения и 18+"),
        OptionSection("substances", "Вещества и зависимости"),
        OptionSection("medical", "Тело и медицина"),
        OptionSection("fears", "Страхи и фобии"),
        OptionSection("family", "Семья и дети"),
        OptionSection("social", "Социальные темы"),
        OptionSection("other", "Прочее"),
        OptionSection("all", "Все по алфавиту"),
    ],
}


GENRE_SECTION_CODES: dict[str, set[str]] = {
    "fantasy_game": {
        "fantasy", "dark_fantasy", "epic_fantasy", "urban_fantasy", "slavic_fantasy", "asian_fantasy",
        "cozy_fantasy", "grimdark", "low_fantasy", "high_fantasy", "historical_fantasy", "romantasy",
        "sword_sorcery", "mythic_fantasy", "gaslamp_fantasy", "flintlock_fantasy", "arcanepunk",
        "lit_rpg", "rpg", "gamelit", "dungeon_core", "progression", "progression_fantasy", "magic_academy",
        "cultivation", "wuxia", "xuanhuan", "isekai", "portal", "reincarnation", "regression",
        "transmigration", "quick_transmigration", "villainess", "apocalypse_system", "supernatural",
    },
    "scifi": {
        "sci_fi", "space", "cyberpunk", "postapoc", "dystopia", "utopia", "steampunk", "biopunk",
        "solarpunk", "time_travel", "alternate_history", "ai", "mecha", "military_sci_fi", "hard_sci_fi",
        "soft_sci_fi", "cli_fi", "speculative_fiction", "near_future", "first_contact", "alien_invasion",
        "planetary_romance", "space_western", "generation_ship", "superhero",
    },
    "romance": {
        "romance", "love_story", "romantasy", "historical_romance", "dark_romance", "sports_romance",
        "office_romance", "romantic_suspense", "monster_romance", "lgbtq_romance", "boys_love", "girls_love",
        "danmei", "omegaverse", "contemporary_romance", "paranormal_romance", "military_romance",
        "royal_romance", "holiday_romance", "regency_romance", "western_romance", "romantic_comedy",
        "forbidden_romance", "erotic",
    },
    "mystery_crime": {
        "detective", "thriller", "psychological_thriller", "domestic_thriller", "mystery", "crime", "noir",
        "police", "cozy_mystery", "cozy_detective", "courtroom", "legal_thriller", "political_thriller",
        "spy", "techno_thriller", "dark_detective", "historical_detective", "procedural", "religious_mystery",
        "hardboiled", "whodunit", "amateur_detective", "crime_drama", "caper", "espionage", "true_crime",
    },
    "horror_mystic": {
        "horror", "dark_horror", "gothic_horror", "cosmic_horror", "space_horror", "paranormal_horror",
        "psychological_horror", "body_horror", "folk_horror", "survival_horror", "slasher", "monster_horror",
        "occult", "cozy_mystic", "analog_horror", "tech_horror", "occult_horror", "possession_horror",
        "weird_fiction", "dark_academia", "adult_dark",
    },
    "contemporary_drama": {
        "drama", "melodrama", "comedy", "slice", "family", "psychological", "medical_drama", "school_life",
        "college", "slice_healing", "literary_fiction", "experimental", "magical_realism", "surreal",
        "contemporary_fiction", "social_fiction", "philosophical_fiction", "domestic_fiction", "coming_of_age",
        "workplace_fiction", "village_prose", "immigrant_fiction", "family_drama", "tragicomedy", "absurdism",
    },
    "historical_adventure": {
        "action", "adventure", "war", "martial", "survival", "western", "historical", "mythology", "fairytale",
        "fairy_retelling", "myth_retelling", "ancient_fiction", "medieval_fiction", "regency_fiction",
        "victorian_fiction", "twentieth_century_fiction", "historical_adventure", "pirate_adventure",
        "treasure_hunt", "expedition", "disaster", "sea_adventure",
    },
    "young_children": {
        "young_adult", "new_adult", "teen", "children", "middle_grade", "picture_book", "bedtime_stories",
        "educational_children", "animal_stories", "school_life", "college", "fairytale",
    },
    "nonfiction": {
        "nonfiction", "self_dev", "business", "popular_science", "biography", "memoir", "history_nonfiction",
        "psychology_nonfiction", "philosophy_nonfiction", "religion_nonfiction", "economics", "finance", "career",
        "parenting", "health", "medicine_nonfiction", "travelogue", "cooking_nonfiction", "technology",
        "programming", "art_nonfiction", "music_nonfiction", "cinema_nonfiction", "journalism", "essays",
        "documentary", "true_story", "education", "reference_nonfiction", "true_crime",
    },
    "humor_poetry": {
        "humor", "satire", "parody", "comedy", "tragicomedy", "lyric_poetry", "narrative_poetry",
        "prose_poetry", "song_lyrics",
    },
    "asian_formats": {
        "manhwa", "manhua", "manga_jp", "webnovel", "light_novel", "ranobe", "danmei", "wuxia",
        "xuanhuan", "cultivation", "isekai", "transmigration", "quick_transmigration", "villainess",
    },
}

TROPE_SECTION_CODES: dict[str, set[str]] = {
    "hero": {
        "strong_mc", "weak_to_strong", "antihero", "villain_mc", "smart_mc", "overpowered", "chosen_one",
        "hidden_power", "secret_identity", "fake_villain", "morally_gray", "female_lead", "male_lead",
        "dual_leads", "ensemble_cast", "underdog", "reluctant_hero", "fallen_hero", "prodigy",
        "fish_out_of_water", "double_life", "identity_reveal", "redemption", "revenge",
    },
    "power_magic": {
        "system", "levels", "skills", "necromancer", "summoner", "mage", "assassin", "healer", "beast_tamer",
        "training", "crafting", "alchemy", "artifacts", "pets", "forbidden_magic", "bloodline", "demon_contract",
        "magic_contract", "prophecy", "reincarnated_villain", "system_apocalypse", "dungeon_core_mc",
        "cultivation_path", "regression_to_past", "transmigration_world", "summoned_hero", "magical_school",
    },
    "world_society": {
        "dungeon", "tower", "guilds", "clans", "sects", "kingdom", "empire", "nobility", "politics",
        "dragons", "demons", "angels", "undead", "vampires", "werewolves", "gods", "ancient_ruins",
        "sealed_evil", "military", "strategy", "base_building", "kingdom_building", "court_intrigue", "merchant",
        "naval", "rebellion", "revolution", "civil_war", "colonization", "dynasty",
    },
    "quest_conflict": {
        "academy_arc", "tournament", "survival_game", "conspiracy", "secret_society", "heist", "road", "sea",
        "quest", "rescue_mission", "escape", "chase", "apocalypse", "catastrophe", "parallel_worlds",
        "multiverse", "simulation", "competition", "political_campaign",
    },
    "romance": {
        "romance_slow", "love_triangle", "enemies_lovers", "friends_lovers", "fake_relationship", "harem",
        "reverse_harem", "no_romance", "contract_marriage", "second_chance", "rivals", "forced_proximity",
        "arranged_marriage", "secret_child", "forbidden_love", "grumpy_sunshine", "opposites_attract", "one_bed",
        "roommates", "bodyguard_romance", "royal_commoner", "single_parent", "secret_romance",
        "marriage_of_convenience", "workplace_romance", "holiday_romance",
    },
    "family": {
        "found_family", "mentor_student", "inheritance", "small_town", "siblings", "parent_child", "adoption",
        "family_secret", "dynasty", "healing_journey",
    },
    "mystery_crime": {
        "detective_case", "murder_mystery", "psychological_games", "serial_killer", "conspiracy", "secret_society",
        "locked_room", "cold_case", "heist", "cozy_mystery_case", "dark_investigation", "missing_person",
        "closed_circle", "legal_case", "cybercrime", "dark_secret",
    },
    "horror": {
        "zombies", "monsters", "ghosts", "curses", "haunted_house", "found_footage", "isolated_place", "snowed_in",
        "evil_cult", "ancient_god", "cursed_object", "occult_investigation", "possession", "ritual", "cosmic_threat",
        "slasher_chase", "urban_legend", "creature_feature",
    },
    "scifi": {
        "robot_companion", "alien_contact", "virtual_world", "space_station", "lost_colony", "time_loop",
        "body_swap", "parallel_worlds", "multiverse", "simulation", "first_contact_trope", "generation_ship_trope",
        "terraforming", "android_mc", "clone", "genetic_engineering", "ai_uprising", "posthuman",
        "cybercrime", "virtual_reality",
    },
    "everyday": {
        "business_building", "farm", "cooking", "medicine", "sports", "music", "showbiz", "school", "workplace",
        "sports_team", "competition", "career_growth", "startup", "restaurant", "hospital", "legal_case",
        "village_life", "small_town",
    },
    "structure": {
        "slow_burn_plot", "plot_twists", "cliffhangers", "multiple_pov", "unreliable_narrator", "memory_loss",
        "time_loop", "episodic", "nonlinear", "story_with_choices", "multiple_endings", "framed_story",
        "letters_diary", "document_style",
    },
    "nonfiction": {
        "practical_steps", "exercises", "checklists", "case_studies", "personal_experience", "interviews",
        "research_based", "reference_format", "beginner_friendly", "document_style",
    },
}

AUDIENCE_SECTION_CODES: dict[str, set[str]] = {
    "age": {
        "kids_0_6", "kids_7_9", "kids_10_12", "teens_13_15", "teens_16_17", "teen", "young", "adult",
        "mature", "adults_18_24", "adults_25_34", "adults_35_49", "adults_50_plus", "family_reading",
    },
    "interests": {
        "all", "male", "female", "romance_fans", "action_fans", "horror_fans", "detective_fans",
        "dark_fantasy_fans", "cultivation_fans", "romantasy_fans", "manga_fans", "nonfiction_readers",
        "professionals", "parents", "educators", "complex_world_fans",
    },
    "reading_style": {
        "casual", "deep", "fast", "slow", "series_fans", "night_readers", "short_sessions", "completed_only",
        "beginner_readers", "experienced_readers", "binge_readers", "cozy_readers", "free_first", "premium",
    },
    "format": {"audio_fans", "commute_audio", "short_audio", "long_audio", "manga_fans"},
}

WARNING_SECTION_CODES: dict[str, set[str]] = {
    "violence": {
        "violence", "graphic_violence", "blood", "gore", "death", "war", "torture", "abuse",
        "domestic_violence", "child_abuse", "sexual_violence", "coercion", "kidnapping", "captivity",
        "slavery", "terrorism", "natural_disaster", "fire", "drowning",
    },
    "mental": {
        "trauma", "dark", "horror", "psych_pressure", "manipulation", "selfharm", "suicide", "depression",
        "panic_attacks", "ptsd", "eating_disorder", "mental_illness", "grief",
    },
    "relationships": {
        "adult", "explicit_sex", "sexual_content", "toxic_relationship", "stalking", "harassment", "infidelity",
        "age_gap", "pregnancy", "miscarriage", "infertility", "childbirth", "abortion",
    },
    "substances": {"drugs", "drug_use", "alcohol", "smoking", "addiction", "gambling"},
    "medical": {
        "body_horror", "medical", "serious_illness", "cancer", "surgery", "needles", "vomiting", "blood",
        "gore", "pregnancy", "childbirth",
    },
    "fears": {"claustrophobia", "insects", "spiders", "snakes", "heights", "drowning", "fire"},
    "family": {
        "animal_death", "animal_harm", "child_death", "parent_death", "child_abuse", "pregnancy", "miscarriage",
        "infertility", "childbirth", "abortion", "grief",
    },
    "social": {
        "religion", "politics", "religious_conflict", "political_extremism", "discrimination", "racism",
        "xenophobia", "homophobia", "bullying", "slavery", "terrorism", "language",
    },
    "other": {"none", "spoilers", "major_spoiler_theme"},
}


BOOK_TYPE_GENRE_RECOMMENDATIONS: dict[str, tuple[str, ...]] = {
    "novel": ("contemporary_fiction", "fantasy", "sci_fi", "romance", "detective", "thriller", "historical", "adventure", "drama", "literary_fiction"),
    "serial": ("webnovel", "fantasy", "lit_rpg", "romance", "detective", "sci_fi", "adventure", "cultivation", "slice"),
    "web_serial": ("webnovel", "lit_rpg", "cultivation", "romance", "fantasy", "sci_fi", "isekai", "progression_fantasy"),
    "story": ("contemporary_fiction", "drama", "romance", "detective", "horror", "adventure", "literary_fiction"),
    "novella": ("contemporary_fiction", "romance", "drama", "mystery", "horror", "sci_fi", "fantasy"),
    "short_story": ("contemporary_fiction", "humor", "horror", "sci_fi", "romance", "fairytale"),
    "shorts": ("contemporary_fiction", "humor", "horror", "sci_fi", "romance", "fairytale", "children"),
    "anthology": ("literary_fiction", "horror", "sci_fi", "fantasy", "romance", "poetry"),
    "poetry": ("lyric_poetry", "narrative_poetry", "prose_poetry", "song_lyrics"),
    "light_novel_type": ("light_novel", "ranobe", "isekai", "fantasy", "romance", "school_life", "lit_rpg"),
    "interactive": ("adventure", "detective", "fantasy", "horror", "survival", "sci_fi"),
    "diary": ("memoir", "biography", "contemporary_fiction", "travelogue", "true_story"),
    "epistolary": ("literary_fiction", "romance", "mystery", "historical", "drama"),
    "play": ("drama", "comedy", "tragedy", "historical", "romance"),
    "screenplay": ("drama", "comedy", "thriller", "action", "crime", "romance"),
    "essay": ("essays", "philosophy_nonfiction", "social_fiction", "journalism", "literary_fiction"),
    "nonfiction_book": ("nonfiction", "popular_science", "history_nonfiction", "psychology_nonfiction", "business", "biography"),
    "guide": ("self_dev", "business", "career", "health", "parenting", "technology", "cooking_nonfiction"),
    "reference": ("reference_nonfiction", "popular_science", "history_nonfiction", "technology", "education"),
    "biography_type": ("biography", "memoir", "true_story", "history_nonfiction", "documentary"),
    "educational": ("education", "popular_science", "reference_nonfiction", "technology", "programming", "history_nonfiction"),
    "children_picture": ("picture_book", "children", "bedtime_stories", "educational_children", "animal_stories", "fairytale"),
    "fanfic": ("romance", "fantasy", "adventure", "drama", "comedy", "alternate_history"),
    "translation": ("literary_fiction", "fantasy", "romance", "sci_fi", "detective", "nonfiction"),
    "audio": ("audio_drama", "fiction", "nonfiction", "mystery", "romance", "self_dev"),
    "audio_play": ("audio_drama", "drama", "mystery", "comedy", "horror", "children"),
    "mixed": ("fantasy", "romance", "detective", "sci_fi", "nonfiction", "audio_drama"),
}

GENRE_TROPE_RECOMMENDATIONS: dict[str, tuple[str, ...]] = {
    "fantasy": ("chosen_one", "magic_contract", "quest", "artifacts", "dragons", "gods", "hidden_power", "found_family"),
    "dark_fantasy": ("morally_gray", "forbidden_magic", "demons", "curses", "ancient_god", "sealed_evil", "revenge"),
    "lit_rpg": ("system", "levels", "skills", "dungeon", "guilds", "weak_to_strong", "crafting", "tournament"),
    "cultivation": ("cultivation_path", "sects", "clans", "training", "alchemy", "weak_to_strong", "bloodline"),
    "isekai": ("summoned_hero", "fish_out_of_water", "system", "parallel_worlds", "magic_contract", "found_family"),
    "reincarnation": ("regression_to_past", "hidden_power", "second_chance", "revenge", "secret_identity"),
    "romance": ("romance_slow", "enemies_lovers", "friends_lovers", "second_chance", "forced_proximity", "found_family"),
    "dark_romance": ("morally_gray", "enemies_lovers", "toxic_relationship", "secret_romance", "forced_proximity"),
    "detective": ("detective_case", "murder_mystery", "closed_circle", "plot_twists", "cold_case", "missing_person"),
    "thriller": ("conspiracy", "chase", "dark_secret", "unreliable_narrator", "plot_twists", "cliffhangers"),
    "horror": ("isolated_place", "cursed_object", "haunted_house", "survival_game", "urban_legend", "creature_feature"),
    "sci_fi": ("alien_contact", "robot_companion", "ai_uprising", "virtual_reality", "terraforming", "space_station"),
    "postapoc": ("apocalypse", "base_building", "survival_game", "zombies", "monsters", "found_family"),
    "historical": ("politics", "court_intrigue", "war", "dynasty", "secret_identity", "family_secret"),
    "adventure": ("quest", "rescue_mission", "road", "sea", "treasure_hunt", "chase", "found_family"),
    "children": ("found_family", "pets", "adventure", "school", "healing_journey"),
    "nonfiction": ("research_based", "case_studies", "personal_experience", "practical_steps", "interviews"),
    "self_dev": ("practical_steps", "exercises", "checklists", "case_studies", "beginner_friendly"),
    "business": ("case_studies", "practical_steps", "checklists", "startup", "career_growth"),
    "biography": ("personal_experience", "document_style", "family_secret", "career_growth", "healing_journey"),
}

GENRE_WARNING_RECOMMENDATIONS: dict[str, tuple[str, ...]] = {
    "horror": ("horror", "dark", "blood", "death", "graphic_violence", "body_horror"),
    "dark_horror": ("horror", "dark", "gore", "death", "torture", "body_horror"),
    "dark_fantasy": ("dark", "violence", "blood", "death", "war"),
    "grimdark": ("dark", "graphic_violence", "gore", "war", "torture"),
    "war": ("war", "violence", "death", "trauma", "ptsd"),
    "crime": ("violence", "death", "drugs", "language"),
    "thriller": ("violence", "death", "psych_pressure", "stalking", "kidnapping"),
    "dark_romance": ("toxic_relationship", "manipulation", "stalking", "coercion", "adult"),
    "erotic": ("adult", "explicit_sex", "sexual_content"),
    "medical_drama": ("medical", "blood", "serious_illness", "surgery", "death"),
    "body_horror": ("body_horror", "gore", "medical", "vomiting"),
    "postapoc": ("violence", "death", "natural_disaster", "horror", "blood"),
    "psychological": ("trauma", "depression", "panic_attacks", "psych_pressure"),
    "true_crime": ("violence", "death", "abuse", "sexual_violence", "kidnapping"),
}

TROPE_WARNING_RECOMMENDATIONS: dict[str, tuple[str, ...]] = {
    "serial_killer": ("violence", "death", "blood", "graphic_violence"),
    "slasher_chase": ("graphic_violence", "blood", "gore", "death"),
    "war": ("war", "violence", "death", "trauma"),
    "toxic_relationship": ("toxic_relationship", "manipulation", "psych_pressure"),
    "stalking": ("stalking", "psych_pressure"),
    "body_horror": ("body_horror", "gore", "medical"),
    "zombies": ("horror", "blood", "gore", "death"),
    "torture": ("torture", "graphic_violence"),
    "suicide": ("suicide", "selfharm", "depression"),
}


def _choice_codes(choices: list[Choice]) -> set[str]:
    return {item.code for item in choices}


def _valid_codes(codes, choices: list[Choice]) -> set[str]:
    valid = _choice_codes(choices)
    return {str(code) for code in codes or () if str(code) in valid}


def section_label(prefix: str, section_code: str) -> str:
    if section_code == "selected":
        return "Выбрано"
    for section in OPTION_SECTIONS.get(prefix, []):
        if section.code == section_code:
            return section.label
    return "Все"


def sections_for_prefix(prefix: str, selected: set[str] | list[str] | tuple[str, ...] = ()) -> list[OptionSection]:
    sections = list(OPTION_SECTIONS.get(prefix, []))
    if selected:
        sections.insert(1, OptionSection("selected", "Выбрано"))
    return sections


def recommended_genre_codes(book_type_codes=()) -> set[str]:
    result: set[str] = set()
    for book_type in book_type_codes or ():
        result.update(BOOK_TYPE_GENRE_RECOMMENDATIONS.get(str(book_type), ()))
    if not result:
        result.update(("fantasy", "romance", "detective", "sci_fi", "adventure", "contemporary_fiction", "nonfiction", "children"))
    return _valid_codes(result, GENRES)


def recommended_trope_codes(book_type_codes=(), genre_codes=()) -> set[str]:
    result: set[str] = set()
    for genre in genre_codes or ():
        result.update(GENRE_TROPE_RECOMMENDATIONS.get(str(genre), ()))
        for section, section_codes in GENRE_SECTION_CODES.items():
            if str(genre) in section_codes:
                if section == "fantasy_game":
                    result.update(("weak_to_strong", "hidden_power", "training", "quest", "found_family", "morally_gray"))
                elif section == "scifi":
                    result.update(("alien_contact", "robot_companion", "virtual_world", "conspiracy", "survival_game"))
                elif section == "romance":
                    result.update(("romance_slow", "enemies_lovers", "friends_lovers", "second_chance", "forced_proximity"))
                elif section == "mystery_crime":
                    result.update(("detective_case", "murder_mystery", "plot_twists", "dark_secret", "conspiracy"))
                elif section == "horror_mystic":
                    result.update(("isolated_place", "curses", "ghosts", "survival_game", "dark_secret"))
                elif section == "nonfiction":
                    result.update(("research_based", "case_studies", "personal_experience", "practical_steps", "checklists"))
    for book_type in book_type_codes or ():
        if str(book_type) == "interactive":
            result.update(("story_with_choices", "multiple_endings", "quest", "survival_game", "plot_twists"))
        elif str(book_type) in {"diary", "epistolary"}:
            result.update(("letters_diary", "document_style", "unreliable_narrator", "family_secret"))
        elif str(book_type) in {"guide", "educational", "reference", "nonfiction_book"}:
            result.update(("practical_steps", "exercises", "checklists", "case_studies", "research_based", "reference_format"))
    if not result:
        result.update(("strong_mc", "weak_to_strong", "found_family", "revenge", "redemption", "plot_twists", "slow_burn_plot", "multiple_pov"))
    return _valid_codes(result, TROPES)


def recommended_audience_codes(book_type_codes=(), genre_codes=()) -> set[str]:
    result: set[str] = {"all"}
    genre_set = set(genre_codes or ())
    type_set = set(book_type_codes or ())
    if genre_set & GENRE_SECTION_CODES["young_children"] or "children_picture" in type_set:
        result.update(("family_reading", "kids_7_9", "kids_10_12", "teens_13_15"))
    if genre_set & GENRE_SECTION_CODES["romance"]:
        result.update(("romance_fans", "young", "adult", "romantasy_fans"))
    if genre_set & GENRE_SECTION_CODES["fantasy_game"]:
        result.update(("action_fans", "series_fans", "complex_world_fans", "dark_fantasy_fans", "cultivation_fans"))
    if genre_set & GENRE_SECTION_CODES["mystery_crime"]:
        result.update(("detective_fans", "deep", "adult"))
    if genre_set & GENRE_SECTION_CODES["horror_mystic"]:
        result.update(("horror_fans", "mature", "night_readers"))
    if genre_set & GENRE_SECTION_CODES["nonfiction"] or type_set & {"guide", "reference", "educational", "nonfiction_book"}:
        result.update(("nonfiction_readers", "beginner_readers", "experienced_readers", "professionals"))
    if type_set & {"audio", "audio_play", "mixed"}:
        result.update(("audio_fans", "commute_audio"))
    if type_set & {"serial", "web_serial"}:
        result.update(("series_fans", "binge_readers"))
    return _valid_codes(result, AUDIENCES)


def recommended_warning_codes(genre_codes=(), trope_codes=()) -> set[str]:
    result: set[str] = {"none"}
    for genre in genre_codes or ():
        result.update(GENRE_WARNING_RECOMMENDATIONS.get(str(genre), ()))
        if str(genre) in GENRE_SECTION_CODES["horror_mystic"]:
            result.update(("horror", "dark", "death", "blood"))
        elif str(genre) in GENRE_SECTION_CODES["mystery_crime"]:
            result.update(("violence", "death", "psych_pressure", "language"))
        elif str(genre) in GENRE_SECTION_CODES["romance"]:
            result.update(("toxic_relationship", "adult", "sexual_content"))
    for trope in trope_codes or ():
        result.update(TROPE_WARNING_RECOMMENDATIONS.get(str(trope), ()))
        if str(trope) in TROPE_SECTION_CODES["horror"]:
            result.update(("horror", "dark", "death"))
        elif str(trope) in TROPE_SECTION_CODES["quest_conflict"]:
            result.update(("violence", "death"))
    return _valid_codes(result, CONTENT_WARNINGS)


def choices_in_section(
    prefix: str,
    choices: list[Choice],
    section_code: str,
    *,
    selected=(),
    book_type_codes=(),
    genre_codes=(),
    trope_codes=(),
) -> list[Choice]:
    selected_set = _valid_codes(selected, choices)
    if section_code == "selected":
        codes = selected_set
    elif section_code == "recommended":
        if prefix == "g":
            codes = recommended_genre_codes(book_type_codes)
        elif prefix == "t":
            codes = recommended_trope_codes(book_type_codes, genre_codes)
        elif prefix == "a":
            codes = recommended_audience_codes(book_type_codes, genre_codes)
        elif prefix == "c":
            codes = recommended_warning_codes(genre_codes, trope_codes)
        else:
            codes = set()
    elif section_code == "all":
        return sorted(choices, key=lambda item: item.label.casefold())
    else:
        section_map = {
            "g": GENRE_SECTION_CODES,
            "t": TROPE_SECTION_CODES,
            "a": AUDIENCE_SECTION_CODES,
            "c": WARNING_SECTION_CODES,
        }.get(prefix, {})
        codes = set(section_map.get(section_code, set()))
    filtered = [item for item in choices if item.code in codes]
    if section_code == "recommended":
        # Стабильный порядок рекомендаций: сначала уже выбранное, затем по исходному приоритету списка.
        filtered.sort(key=lambda item: (0 if item.code in selected_set else 1, choices.index(item)))
    else:
        filtered.sort(key=lambda item: item.label.casefold())
    return filtered


def suggested_age_limit(warning_codes=(), genre_codes=()) -> str:
    warnings = set(warning_codes or ())
    genres = set(genre_codes or ())
    if warnings & {"adult", "explicit_sex", "sexual_violence", "gore", "torture"}:
        return "18+"
    if warnings & {
        "graphic_violence", "domestic_violence", "child_abuse", "coercion", "selfharm", "suicide",
        "drug_use", "addiction", "eating_disorder", "terrorism", "political_extremism",
    }:
        return "16+"
    if warnings - {"none", "spoilers", "major_spoiler_theme"}:
        return "12+"
    if genres & {"children", "picture_book", "bedtime_stories", "educational_children"}:
        return "6+"
    if genres & (GENRE_SECTION_CODES["horror_mystic"] | GENRE_SECTION_CODES["mystery_crime"]):
        return "12+"
    return "0+"

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
