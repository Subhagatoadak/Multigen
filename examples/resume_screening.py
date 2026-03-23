"""
Resume Screening Pipeline — End-to-End Example
================================================

Demonstrates all 4 Multigen DSL step types:
  ✓ Sequential  — parse resume
  ✓ Parallel    — score skills + experience + culture fit simultaneously
  ✓ Conditional — route to interview or rejection based on overall score
  ✓ Sequential  — compile final report

Usage:
    python examples/resume_screening.py [--candidate strong|weak|borderline]

Requirements:
    pip install httpx
    docker-compose up -d
"""

import argparse
import json

import httpx

ORCHESTRATOR = "http://localhost:8000"
CAPABILITY   = "http://localhost:8001"

# ── Candidate profiles ─────────────────────────────────────────────────────────

CANDIDATES = {
    "strong": {
        "name":             "Alice Chen",
        "email":            "alice@example.com",
        "skills":           ["python", "kafka", "kubernetes", "temporal", "fastapi",
                             "mongodb", "docker", "grpc", "opentelemetry"],
        "years_experience": 7,
        "previous_roles":   ["Senior Software Engineer", "Tech Lead", "Principal Engineer"],
        "values":           ["ownership", "remote-first", "open-source", "continuous-learning"],
    },
    "weak": {
        "name":             "Bob Smith",
        "email":            "bob@example.com",
        "skills":           ["excel", "powerpoint", "basic-python"],
        "years_experience": 1,
        "previous_roles":   ["Junior Analyst"],
        "values":           ["stability"],
    },
    "borderline": {
        "name":             "Carol Davis",
        "email":            "carol@example.com",
        "skills":           ["python", "fastapi", "docker", "postgresql"],
        "years_experience": 3,
        "previous_roles":   ["Software Engineer"],
        "values":           ["remote-first", "open-source"],
    },
}

# ── Agents to register ─────────────────────────────────────────────────────────

AGENTS = [
    {"name": "ResumeParserAgent",        "version": "1.0.0",
     "description": "Parses raw resume into structured profile"},
    {"name": "SkillMatcherAgent",         "version": "1.0.0",
     "description": "Scores skill match against job requirements"},
    {"name": "ExperienceEvaluatorAgent",  "version": "1.0.0", "description": "Scores experience depth and seniority"},
    {"name": "CultureFitAgent",           "version": "1.0.0", "description": "Scores cultural alignment"},
    {"name": "ScoreAggregatorAgent",      "version": "1.0.0", "description": "Combines scores into overall decision"},
    {"name": "InterviewSchedulerAgent",   "version": "1.0.0",
     "description": "Schedules interview for qualified candidates"},
    {"name": "RejectionAgent",            "version": "1.0.0",
     "description": "Generates rejection for unqualified candidates"},
    {"name": "ReportCompilerAgent",       "version": "1.0.0", "description": "Compiles final screening report"},
]

# ── Workflow DSL ───────────────────────────────────────────────────────────────

def build_workflow(candidate: dict) -> dict:
    """
    Builds the full screening DSL for a given candidate.

    Step breakdown:
      1. parse        — sequential: ResumeParserAgent
      2. evaluate     — parallel:   SkillMatcherAgent + ExperienceEvaluatorAgent + CultureFitAgent
      3. aggregate    — sequential: ScoreAggregatorAgent
      4. route        — conditional: InterviewSchedulerAgent OR RejectionAgent
      5. report       — sequential: ReportCompilerAgent
    """
    return {
        "steps": [

            # ── Step 1: Parse the resume ───────────────────────────────────
            {
                "name":  "parse",
                "agent": "ResumeParserAgent",
                "params": candidate,
            },

            # ── Step 2: Parallel evaluation ────────────────────────────────
            {
                "name": "evaluate",
                "parallel": [
                    {
                        "name":  "skill_check",
                        "agent": "SkillMatcherAgent",
                        "params": {
                            "profile": {               # Pre-filled for the demo;
                                "skills":           candidate["skills"],   # in production this would
                                "years_experience": candidate["years_experience"],  # come from step 1 output
                                "previous_roles":   candidate["previous_roles"],
                                "values":           candidate.get("values", []),
                            },
                            "required_skills": ["python", "docker", "fastapi", "kafka"],
                            "nice_to_have":    ["kubernetes", "temporal", "opentelemetry"],
                        },
                    },
                    {
                        "name":  "experience_check",
                        "agent": "ExperienceEvaluatorAgent",
                        "params": {
                            "profile": {
                                "years_experience": candidate["years_experience"],
                                "previous_roles":   candidate["previous_roles"],
                            },
                            "min_years":       3,
                            "preferred_roles": ["senior", "lead", "principal"],
                        },
                    },
                    {
                        "name":  "culture_check",
                        "agent": "CultureFitAgent",
                        "params": {
                            "profile": {"values": candidate.get("values", [])},
                            "company_values": ["ownership", "remote-first", "open-source"],
                        },
                    },
                ],
            },

            # ── Step 3: Aggregate scores ───────────────────────────────────
            {
                "name":  "aggregate",
                "agent": "ScoreAggregatorAgent",
                "params": {
                    # Hardcoded for demo — in production these come from step 2 outputs
                    "skill_score":      _preview_skill_score(candidate),
                    "experience_score": _preview_experience_score(candidate),
                    "culture_score":    _preview_culture_score(candidate),
                    "threshold": 70,
                    "weights": {"skill": 0.5, "experience": 0.3, "culture": 0.2},
                },
            },

            # ── Step 4: Conditional routing ────────────────────────────────
            {
                "name": "route",
                "conditional": [
                    {
                        "condition": "overall_score >= 70",
                        "then": {
                            "name":  "schedule_interview",
                            "agent": "InterviewSchedulerAgent",
                            "params": {
                                "candidate_name":  candidate["name"],
                                "candidate_email": candidate["email"],
                                "overall_score":   _preview_overall_score(candidate),
                                "interview_panel": ["Engineering Director", "Senior Engineer"],
                            },
                        },
                    },
                ],
                "else": {
                    "name":  "send_rejection",
                    "agent": "RejectionAgent",
                    "params": {
                        "candidate_name":  candidate["name"],
                        "candidate_email": candidate["email"],
                        "overall_score":   _preview_overall_score(candidate),
                    },
                },
            },

            # ── Step 5: Compile report ─────────────────────────────────────
            {
                "name":  "report",
                "agent": "ReportCompilerAgent",
                "params": {
                    "candidate_name":  candidate["name"],
                    "overall_score":   _preview_overall_score(candidate),
                    "decision":        "PROCEED" if _preview_overall_score(candidate) >= 70 else "REJECT",
                    "skill_score":     _preview_skill_score(candidate),
                    "experience_score": _preview_experience_score(candidate),
                    "culture_score":   _preview_culture_score(candidate),
                },
            },
        ]
    }


# ── Simple preview scorers (mirror agent logic for DSL params) ─────────────────

def _preview_skill_score(c: dict) -> float:
    required      = {"python", "docker", "fastapi", "kafka"}
    nice_to_have  = {"kubernetes", "temporal", "opentelemetry"}
    skills        = set(s.lower() for s in c.get("skills", []))
    req_score     = len(required & skills) / len(required) * 100
    bonus         = len(nice_to_have & skills) / len(nice_to_have) * 10
    return min(100, round(req_score + bonus, 1))

def _preview_experience_score(c: dict) -> float:
    years = c.get("years_experience", 0)
    return min(100, round(years / 3 * 100, 1))

def _preview_culture_score(c: dict) -> float:
    company  = {"ownership", "remote-first", "open-source"}
    values   = set(v.lower() for v in c.get("values", []))
    matched  = len(company & values)
    return round(matched / len(company) * 100, 1)

def _preview_overall_score(c: dict) -> float:
    return min(100, round(
        _preview_skill_score(c)      * 0.5 +
        _preview_experience_score(c) * 0.3 +
        _preview_culture_score(c)    * 0.2,
        1,
    ))


# ── Registration + submission ──────────────────────────────────────────────────

def register_agents(client: httpx.Client) -> None:
    print("\n📋 Registering agents with Capability Directory...")
    for agent in AGENTS:
        resp = client.post(f"{CAPABILITY}/capabilities",
                           json={**agent, "metadata": {}})
        if resp.status_code in (200, 201):
            print(f"   ✓  {agent['name']}")
        elif resp.status_code == 409:
            print(f"   ·  {agent['name']} (already registered)")
        else:
            print(f"   ✗  {agent['name']} — {resp.status_code}: {resp.text}")


def submit_workflow(client: httpx.Client, candidate: dict) -> str:
    dsl  = build_workflow(candidate)
    resp = client.post(f"{ORCHESTRATOR}/workflows/run", json={"dsl": dsl})
    resp.raise_for_status()
    instance_id = resp.json()["instance_id"]
    return instance_id


def print_dsl(candidate: dict) -> None:
    dsl = build_workflow(candidate)
    print("\n📄 Workflow DSL:")
    print(json.dumps(dsl, indent=2))


# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Resume Screening Pipeline Demo")
    parser.add_argument("--candidate", choices=["strong", "weak", "borderline"],
                        default="strong", help="Which candidate profile to use")
    parser.add_argument("--show-dsl", action="store_true",
                        help="Print the DSL before submitting")
    args = parser.parse_args()

    candidate = CANDIDATES[args.candidate]

    print(f"\n{'='*60}")
    print("  MULTIGEN — Resume Screening Pipeline")
    print(f"  Candidate: {candidate['name']} ({args.candidate} profile)")
    print(f"{'='*60}")

    expected = _preview_overall_score(candidate)
    print("\n📊 Expected scores:")
    print(f"   Skills:      {_preview_skill_score(candidate):.1f}/100")
    print(f"   Experience:  {_preview_experience_score(candidate):.1f}/100")
    print(f"   Culture Fit: {_preview_culture_score(candidate):.1f}/100")
    print(f"   Overall:     {expected:.1f}/100  →  {'✅ INTERVIEW' if expected >= 70 else '❌ REJECT'}")

    if args.show_dsl:
        print_dsl(candidate)

    with httpx.Client(timeout=30) as client:
        register_agents(client)

        print(f"\n🚀 Submitting workflow for {candidate['name']}...")
        instance_id = submit_workflow(client, candidate)
        print(f"   instance_id: {instance_id}")

    print(f"\n{'='*60}")
    print("  Next steps:")
    print("  1. Open Temporal UI → http://localhost:8080")
    print(f"  2. Find workflow ID: {instance_id}")
    print("  3. Watch each step execute in real time")
    print("  4. Check flow-worker logs:")
    print("     docker-compose logs -f flow-worker temporal-worker")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
