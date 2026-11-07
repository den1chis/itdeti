from datetime import date, time

from core.course_schedule import SlotInfo, course_end, pause_window, plan_course


def slot(key, weekday, hour, valid_from, active=True, valid_until=None):
    return SlotInfo(key=key, day_of_week=weekday, start_time=time(hour, 0), valid_from=valid_from, valid_until=valid_until, is_active=active)


TUESDAY = date(2026, 11, 10)  # вторник


def test_weekly_slot_gives_exactly_n_lessons_one_week_apart():
    entries = plan_course([slot("a", 1, 17, TUESDAY)], TUESDAY, 3)
    assert [day for day, _ in entries] == [date(2026, 11, 10), date(2026, 11, 17), date(2026, 11, 24)]
    assert course_end(entries, 3) == date(2026, 11, 24)


def test_count_starts_from_anchor_not_from_slot_start():
    # слот действует давно, а курс назначен позже: старые недели в счёт не идут
    entries = plan_course([slot("a", 1, 17, date(2026, 1, 6))], TUESDAY, 2)
    assert [day for day, _ in entries] == [date(2026, 11, 10), date(2026, 11, 17)]


def test_slot_start_later_than_anchor_is_respected():
    entries = plan_course([slot("a", 1, 17, date(2026, 11, 24))], TUESDAY, 2)
    assert [day for day, _ in entries] == [date(2026, 11, 24), date(2026, 12, 1)]


def test_two_slots_per_week_are_merged_in_order():
    slots = [slot("thu", 3, 18, TUESDAY), slot("tue", 1, 17, TUESDAY)]
    entries = plan_course(slots, TUESDAY, 5)
    assert [day for day, _ in entries] == [
        date(2026, 11, 10), date(2026, 11, 12), date(2026, 11, 17), date(2026, 11, 19), date(2026, 11, 24),
    ]
    assert [s.key for _, s in entries] == ["tue", "thu", "tue", "thu", "tue"]


def test_two_lessons_same_day_are_ordered_by_time():
    slots = [slot("late", 1, 19, TUESDAY), slot("early", 1, 15, TUESDAY)]
    entries = plan_course(slots, TUESDAY, 3)
    assert [(day, s.key) for day, s in entries] == [
        (date(2026, 11, 10), "early"), (date(2026, 11, 10), "late"), (date(2026, 11, 17), "early"),
    ]


def test_closed_slot_counts_only_inside_its_window():
    # вторники с 10.11 по 17.11 уже были, потом расписание заменили на четверги
    old = slot("old", 1, 17, TUESDAY, active=False, valid_until=date(2026, 11, 17))
    new = slot("new", 3, 17, date(2026, 11, 19))
    entries = plan_course([old, new], TUESDAY, 4)
    assert [day for day, _ in entries] == [date(2026, 11, 10), date(2026, 11, 17), date(2026, 11, 19), date(2026, 11, 26)]


def test_active_slot_ignores_its_own_previous_cap():
    capped = slot("a", 1, 17, TUESDAY, valid_until=date(2026, 11, 17))
    entries = plan_course([capped], TUESDAY, 4)
    assert course_end(entries, 4) == date(2026, 12, 1)


def test_inactive_slot_without_end_date_is_ignored():
    assert plan_course([slot("a", 1, 17, TUESDAY, active=False)], TUESDAY, 3) == []


def test_empty_course_or_no_slots_give_no_plan():
    assert plan_course([slot("a", 1, 17, TUESDAY)], TUESDAY, 0) == []
    assert plan_course([], TUESDAY, 5) == []
    assert course_end([], 0) is None


def test_course_end_is_none_when_schedule_cannot_cover_all_lessons():
    closed = slot("old", 1, 17, TUESDAY, active=False, valid_until=date(2026, 11, 17))
    entries = plan_course([closed], TUESDAY, 5)
    assert len(entries) == 2
    assert course_end(entries, 5) is None


def test_forty_two_lessons_means_forty_one_weeks_after_first():
    entries = plan_course([slot("a", 1, 17, TUESDAY)], TUESDAY, 42)
    assert len(entries) == 42
    assert (entries[-1][0] - entries[0][0]).days == 41 * 7


def test_cancelled_lesson_is_skipped_and_course_end_moves_one_week():
    # отменили второй вторник: тема этого урока переходит на следующий вторник, конец курса сдвигается
    slots = [slot("a", 1, 17, TUESDAY)]
    entries = plan_course(slots, TUESDAY, 3, skip={("a", date(2026, 11, 17))})
    assert [day for day, _ in entries] == [date(2026, 11, 10), date(2026, 11, 24), date(2026, 12, 1)]
    assert course_end(entries, 3) == date(2026, 12, 1)


def test_cancelled_lesson_with_two_slots_per_week_moves_to_next_occurrence():
    slots = [slot("tue", 1, 17, TUESDAY), slot("thu", 3, 18, TUESDAY)]
    entries = plan_course(slots, TUESDAY, 3, skip={("tue", date(2026, 11, 10))})
    assert [day for day, _ in entries] == [date(2026, 11, 12), date(2026, 11, 17), date(2026, 11, 19)]


def test_skip_only_affects_the_given_slot_on_the_given_day():
    slots = [slot("early", 1, 15, TUESDAY), slot("late", 1, 19, TUESDAY)]
    entries = plan_course(slots, TUESDAY, 2, skip={("early", TUESDAY)})
    assert [(day, s.key) for day, s in entries] == [(TUESDAY, "late"), (date(2026, 11, 17), "early")]


def test_empty_skip_changes_nothing():
    assert plan_course([slot("a", 1, 17, TUESDAY)], TUESDAY, 3, skip=set()) == plan_course([slot("a", 1, 17, TUESDAY)], TUESDAY, 3)


def test_many_cancelled_lessons_keep_the_total_count():
    skipped = {("a", date(2026, 11, 10)), ("a", date(2026, 11, 17)), ("a", date(2026, 12, 1))}
    entries = plan_course([slot("a", 1, 17, TUESDAY)], TUESDAY, 4, skip=skipped)
    assert len(entries) == 4
    assert [day for day, _ in entries] == [date(2026, 11, 24), date(2026, 12, 8), date(2026, 12, 15), date(2026, 12, 22)]


def test_pause_keeps_today_when_lesson_today_was_not_removed():
    assert pause_window(date(2026, 11, 3), date(2026, 11, 7), removed_today=False) == date(2026, 11, 7)


def test_pause_does_not_count_today_when_todays_lesson_was_removed():
    assert pause_window(date(2026, 11, 3), date(2026, 11, 7), removed_today=True) == date(2026, 11, 6)


def test_pause_drops_slot_that_has_not_started_yet():
    # в базе valid_until не может быть раньше valid_from, поэтому такой слот убирают целиком
    assert pause_window(date(2026, 11, 10), date(2026, 11, 7), removed_today=False) is None
    assert pause_window(date(2026, 11, 7), date(2026, 11, 7), removed_today=True) is None


def test_course_resumes_after_pause_with_a_new_schedule():
    # вторники 10.11 и 17.11 прошли, ученика поставили на паузу, потом задали четверги с 03.12
    old = slot("old", 1, 17, TUESDAY, active=False, valid_until=date(2026, 11, 20))
    new = slot("new", 3, 17, date(2026, 12, 3))
    entries = plan_course([old, new], TUESDAY, 5)
    assert [day for day, _ in entries] == [date(2026, 11, 10), date(2026, 11, 17), date(2026, 12, 3), date(2026, 12, 10), date(2026, 12, 17)]
