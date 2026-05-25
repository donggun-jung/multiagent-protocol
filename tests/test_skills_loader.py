"""Tests for the skills plugin loader."""

from __future__ import annotations

from pathlib import Path

from multiagent_protocol.skills.loader import (
    LoadedSkills,
    load_all,
    load_builtin_skills,
    load_user_skills,
)


def test_builtin_skills_load_without_error():
    skills = load_builtin_skills()
    # All five C-validators + classifier publisher + 3 classifier rules + 2 hooks
    assert len(skills.validators) >= 4
    assert len(skills.classifier_rules) >= 2
    assert len(skills.branch_hooks) >= 1


def test_builtin_skill_names_are_unique():
    skills = load_builtin_skills()
    names = (
        [v.name for v in skills.validators]
        + [r.name for r in skills.classifier_rules]
        + [h.name for h in skills.branch_hooks]
    )
    assert len(names) == len(set(names))


def test_load_all_with_no_user_skills_dir(tmp_path: Path):
    # config_root points to an empty tmp dir; user skills missing is fine.
    skills = load_all(config_root=tmp_path / "skills")
    assert skills.total() >= 6
    assert skills.user_skill_load_failures == []


def test_user_skill_with_blocked_network_import_rejected(tmp_path: Path):
    skills_root = tmp_path / "skills"
    validators_dir = skills_root / "validators"
    validators_dir.mkdir(parents=True)
    (validators_dir / "bad_skill.py").write_text(
        "import requests\n"
        "from multiagent_protocol.skills.base import ValidationResult\n"
        "class BadSkill:\n"
        "    name = 'bad'\n"
        "    severity = 'P2'\n"
        "    def check(self, ctx):\n"
        "        return ValidationResult.ok()\n",
        encoding="utf-8",
    )
    user = load_user_skills(skills_root)
    assert len(user.validators) == 0
    assert len(user.user_skill_load_failures) == 1
    assert "requests" in user.user_skill_load_failures[0][1]


def test_user_skill_with_syntax_error_rejected_softly(tmp_path: Path):
    skills_root = tmp_path / "skills"
    validators_dir = skills_root / "validators"
    validators_dir.mkdir(parents=True)
    (validators_dir / "broken_skill.py").write_text(
        "this is not python\n",
        encoding="utf-8",
    )
    user = load_user_skills(skills_root)
    assert len(user.validators) == 0
    assert len(user.user_skill_load_failures) == 1


def test_valid_user_skill_loads(tmp_path: Path):
    skills_root = tmp_path / "skills"
    validators_dir = skills_root / "validators"
    validators_dir.mkdir(parents=True)
    (validators_dir / "ok_skill.py").write_text(
        "from multiagent_protocol.skills.base import ValidationResult\n"
        "class OkSkill:\n"
        "    name = 'ok_skill'\n"
        "    severity = 'P2'\n"
        "    def check(self, ctx):\n"
        "        return ValidationResult.ok()\n",
        encoding="utf-8",
    )
    user = load_user_skills(skills_root)
    assert len(user.validators) == 1
    assert user.validators[0].name == "ok_skill"
    assert user.user_skill_load_failures == []


def test_loaded_skills_total():
    s = LoadedSkills()
    assert s.total() == 0
    builtin = load_builtin_skills()
    assert builtin.total() > 0
