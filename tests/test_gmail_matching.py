from hermes.gmail import choose_match, classify


ROLES = [
    {
        "id": "acme-role",
        "company": "Acme Corporation",
        "title": "Machine Learning Intern",
        "url": "https://jobs.lever.co/acme/REQ-12345",
        "applied": "2026-08-14",
    },
    {
        "id": "acme-other",
        "company": "Acme Corporation",
        "title": "Software Engineering Intern",
        "url": "https://jobs.lever.co/acme/REQ-99999",
        "applied": "2026-08-14",
    },
]


def test_classification():
    assert classify("Unfortunately, we will not be moving forward") == "rejected"
    assert classify("Please schedule your technical interview") == "phone_screen"
    assert classify("We are pleased to extend a formal offer") == "offer"
    assert classify("Thank you for applying; your application was received") == "received"


def test_requisition_produces_unique_high_confidence_match():
    role, confidence, evidence, unique = choose_match(
        ROLES,
        "Recruiting <jobs@acme.com>",
        "Update for Machine Learning Intern",
        "Regarding application REQ-12345 at Acme",
    )
    assert role["id"] == "acme-role"
    assert confidence >= 0.9
    assert "requisition" in evidence
    assert unique


def test_same_company_without_role_evidence_is_ambiguous():
    _, confidence, _, unique = choose_match(
        ROLES, "jobs@acme.com", "Application update", "An update from Acme"
    )
    assert confidence >= 0.5
    assert not unique


def test_unrelated_mail_has_no_match():
    role, confidence, _, unique = choose_match(
        ROLES, "newsletter@example.org", "Weekly digest", "Hello subscriber"
    )
    assert role is None
    assert confidence == 0
    assert not unique
