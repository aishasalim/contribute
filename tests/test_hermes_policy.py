from hermes.policy import audit_answer, classify


PROFILE = {
    "identity": {
        "email": "aisha@example.com",
        "first_name": "Aisha",
        "age": 21,
        "age_18_or_older": True,
        "birth_date": "2005-08-11",
    },
    "eligibility": {
        "authorized_to_work_us": True,
        "requires_sponsorship": False,
        "us_citizen": False,
        "permanent_resident": True,
    },
    "demographics": {
        "gender": "Female",
        "veteran_status": "I am not a protected veteran",
    },
    "availability": {
        "willing_to_relocate": True,
        "summer_2027": True,
        "start_date": "2027-05-10",
        "end_date": "2027-08-25",
        "start_month": "May",
        "start_year": 2027,
    },
    "compensation": {
        "hourly_rate": 30,
        "salary_expectation": "$30 per hour",
    },
}


def test_known_profile_field_is_fillable_without_storing_raw_value():
    decision = classify("Email address", "email", True, PROFILE)
    redacted, digest = audit_answer(decision)
    assert decision.disposition == "filled"
    assert decision.profile_key == "identity.email"
    assert redacted == "<from identity.email>"
    assert digest and "aisha" not in digest


def test_required_free_text_stops():
    decision = classify("Why do you want to work here?", "textarea", True, PROFILE)
    assert decision.category == "free_text"
    assert decision.blocks


def test_cover_letter_upload_is_not_treated_as_resume():
    decision = classify("Upload a cover letter", "file", True, PROFILE)
    assert decision.category == "free_text"
    assert decision.blocks


def test_optional_free_text_is_skipped():
    decision = classify("Additional information", "textarea", False, PROFILE)
    assert decision.disposition == "skipped_optional"


def test_demographic_answers_are_never_returned():
    decision = classify("Race / ethnicity", "select", False, PROFILE)
    assert decision.category == "demographic"
    assert decision.disposition == "skipped_demographic"
    assert audit_answer(decision) == (None, None)


def test_known_demographic_can_be_filled_but_is_redacted():
    decision = classify("Gender", "select", True, PROFILE)
    assert decision.category == "demographic"
    assert decision.disposition == "filled"
    assert decision.value == "Female"
    assert audit_answer(decision) == (None, None)


def test_combined_citizen_or_permanent_resident_question_is_true():
    decision = classify(
        "Are you a U.S. citizen or permanent resident?", "radio", True, PROFILE
    )
    assert decision.disposition == "filled"
    assert decision.profile_key == "eligibility.permanent_resident"
    assert decision.value is True


def test_common_availability_and_compensation_answers():
    relocation = classify("Are you willing to relocate?", "radio", True, PROFILE)
    start = classify("Available start date", "date", True, PROFILE)
    pay = classify("Desired hourly rate", "number", True, PROFILE)
    adult = classify("Are you at least 18 years old?", "radio", True, PROFILE)

    assert relocation.value is True
    assert start.value == "2027-05-10"
    assert pay.value == 30
    assert adult.value is True


def test_birth_date_and_internship_end_date():
    birth = classify("Date of birth", "date", True, PROFILE)
    end = classify("Availability end date", "date", True, PROFILE)

    assert birth.value == "2005-08-11"
    assert end.value == "2027-08-25"


def test_country_word_does_not_override_work_authorization_answer():
    authorization = classify(
        "Are you legally authorized to work in the country where this role is based?",
        "combobox",
        True,
        PROFILE,
    )
    sponsorship = classify(
        "Will you require sponsorship in this country?", "combobox", True, PROFILE
    )
    assert authorization.value is True
    assert sponsorship.value is False


def test_split_start_date_fields_use_component_values():
    month = classify("Start date month", "combobox", True, PROFILE)
    year = classify("Start date year", "number", True, PROFILE)
    assert month.value == "May"
    assert year.value == 2027


def test_unknown_required_field_stops():
    assert classify("Favorite compiler mascot", "text", True, PROFILE).blocks
