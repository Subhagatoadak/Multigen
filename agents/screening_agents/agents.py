"""
Resume Screening Pipeline — Agent Implementations

All agents are pure Python (no LLM required) so the example works
out of the box without any API keys.

Agents:
  ResumeParserAgent        — normalises raw resume input into structured fields
  SkillMatcherAgent        — scores how well candidate skills match the job
  ExperienceEvaluatorAgent — scores years + seniority of experience
  CultureFitAgent          — scores culture fit from values / interests
  ScoreAggregatorAgent     — combines parallel scores into an overall decision
  InterviewSchedulerAgent  — books an interview slot for qualified candidates
  RejectionAgent           — generates a polite rejection for unqualified ones
  ReportCompilerAgent      — assembles the final screening report
"""

from typing import Any, Dict
from orchestrator.services.agent_registry import register_agent
from agents.base_agent import BaseAgent


# ── 1. Resume Parser ──────────────────────────────────────────────────────────

@register_agent("ResumeParserAgent")
class ResumeParserAgent(BaseAgent):
    """
    Normalises raw resume data into a structured candidate profile.

    Input params:
        name (str)
        email (str)
        skills (list[str])
        years_experience (int)
        previous_roles (list[str])
        values (list[str])          optional — e.g. ["remote-first", "ownership"]
    """

    async def run(self, params: Dict[str, Any]) -> Dict[str, Any]:
        skills = [s.lower().strip() for s in params.get("skills", [])]
        roles  = params.get("previous_roles", [])

        profile = {
            "name":             params.get("name", "Unknown"),
            "email":            params.get("email", ""),
            "skills":           skills,
            "skill_count":      len(skills),
            "years_experience": int(params.get("years_experience", 0)),
            "previous_roles":   roles,
            "role_count":       len(roles),
            "values":           [v.lower().strip() for v in params.get("values", [])],
            "parsed":           True,
        }

        self.logger.info("Parsed resume for %s — %d skills, %d years",
                         profile["name"], profile["skill_count"], profile["years_experience"])
        return {"profile": profile}


# ── 2. Skill Matcher ──────────────────────────────────────────────────────────

@register_agent("SkillMatcherAgent")
class SkillMatcherAgent(BaseAgent):
    """
    Scores how well the candidate's skills match the job requirements.

    Input params:
        profile (dict)              output of ResumeParserAgent
        required_skills (list[str]) skills the role demands
        nice_to_have (list[str])    bonus skills
    """

    async def run(self, params: Dict[str, Any]) -> Dict[str, Any]:
        profile        = params.get("profile", {})
        required       = [s.lower() for s in params.get("required_skills", [])]
        nice_to_have   = [s.lower() for s in params.get("nice_to_have", [])]
        candidate      = set(profile.get("skills", []))

        matched_required  = [s for s in required      if s in candidate]
        matched_bonus     = [s for s in nice_to_have  if s in candidate]
        missing_required  = [s for s in required      if s not in candidate]

        required_score = (len(matched_required) / len(required) * 100) if required else 100
        bonus_score    = (len(matched_bonus)    / len(nice_to_have) * 10) if nice_to_have else 0
        final_score    = min(100, round(required_score + bonus_score, 1))

        self.logger.info("Skill match score: %.1f (required: %d/%d, bonus: %d/%d)",
                         final_score, len(matched_required), len(required),
                         len(matched_bonus), len(nice_to_have))

        return {
            "skill_score":       final_score,
            "matched_required":  matched_required,
            "matched_bonus":     matched_bonus,
            "missing_required":  missing_required,
        }


# ── 3. Experience Evaluator ───────────────────────────────────────────────────

@register_agent("ExperienceEvaluatorAgent")
class ExperienceEvaluatorAgent(BaseAgent):
    """
    Scores candidate experience against role requirements.

    Input params:
        profile (dict)
        min_years (int)     minimum years required
        preferred_roles (list[str])   seniority keywords e.g. ["senior", "lead", "principal"]
    """

    async def run(self, params: Dict[str, Any]) -> Dict[str, Any]:
        profile         = params.get("profile", {})
        min_years       = int(params.get("min_years", 3))
        preferred_roles = [r.lower() for r in params.get("preferred_roles", [])]

        years  = profile.get("years_experience", 0)
        roles  = [r.lower() for r in profile.get("previous_roles", [])]

        # Years score: 100 if at/above min, proportional below
        years_score = min(100, round((years / min_years) * 100, 1)) if min_years else 100

        # Seniority bonus: +10 per matched preferred role keyword (max 20)
        seniority_matches = [r for r in preferred_roles
                             if any(r in role for role in roles)]
        seniority_bonus   = min(20, len(seniority_matches) * 10)

        final_score = min(100, round(years_score + seniority_bonus, 1))

        self.logger.info("Experience score: %.1f (%d years vs %d required)",
                         final_score, years, min_years)

        return {
            "experience_score":    final_score,
            "years_experience":    years,
            "min_years_required":  min_years,
            "meets_min_years":     years >= min_years,
            "seniority_matches":   seniority_matches,
        }


# ── 4. Culture Fit ────────────────────────────────────────────────────────────

@register_agent("CultureFitAgent")
class CultureFitAgent(BaseAgent):
    """
    Scores cultural alignment between candidate values and company values.

    Input params:
        profile (dict)
        company_values (list[str])   e.g. ["ownership", "remote-first", "open-source"]
    """

    async def run(self, params: Dict[str, Any]) -> Dict[str, Any]:
        profile         = params.get("profile", {})
        company_values  = [v.lower() for v in params.get("company_values", [])]
        candidate       = set(profile.get("values", []))

        matched = [v for v in company_values if v in candidate]
        score   = round((len(matched) / len(company_values)) * 100, 1) if company_values else 50

        self.logger.info("Culture fit score: %.1f (%d/%d values aligned)",
                         score, len(matched), len(company_values))

        return {
            "culture_score":   score,
            "matched_values":  matched,
            "total_values":    len(company_values),
        }


# ── 5. Score Aggregator ───────────────────────────────────────────────────────

@register_agent("ScoreAggregatorAgent")
class ScoreAggregatorAgent(BaseAgent):
    """
    Combines parallel evaluation scores into a weighted overall score
    and makes a hire / no-hire decision.

    Input params:
        skill_score      (float)  from SkillMatcherAgent
        experience_score (float)  from ExperienceEvaluatorAgent
        culture_score    (float)  from CultureFitAgent
        threshold        (int)    minimum score to proceed (default 70)
        weights          (dict)   optional {"skill": 0.5, "experience": 0.3, "culture": 0.2}
    """

    async def run(self, params: Dict[str, Any]) -> Dict[str, Any]:
        skill_score      = float(params.get("skill_score", 0))
        experience_score = float(params.get("experience_score", 0))
        culture_score    = float(params.get("culture_score", 0))
        threshold        = int(params.get("threshold", 70))
        weights          = params.get("weights", {"skill": 0.5, "experience": 0.3, "culture": 0.2})

        overall = round(
            skill_score      * weights.get("skill", 0.5) +
            experience_score * weights.get("experience", 0.3) +
            culture_score    * weights.get("culture", 0.2),
            1,
        )

        decision = "PROCEED" if overall >= threshold else "REJECT"

        self.logger.info("Aggregate score: %.1f (threshold: %d) → %s",
                         overall, threshold, decision)

        return {
            "overall_score":     overall,
            "threshold":         threshold,
            "decision":          decision,
            "skill_score":       skill_score,
            "experience_score":  experience_score,
            "culture_score":     culture_score,
        }


# ── 6. Interview Scheduler ────────────────────────────────────────────────────

@register_agent("InterviewSchedulerAgent")
class InterviewSchedulerAgent(BaseAgent):
    """
    Schedules an interview for qualified candidates.

    Input params:
        candidate_name  (str)
        candidate_email (str)
        overall_score   (float)
        interview_panel (list[str])  optional
    """

    async def run(self, params: Dict[str, Any]) -> Dict[str, Any]:
        name   = params.get("candidate_name", "Candidate")
        email  = params.get("candidate_email", "")
        score  = params.get("overall_score", 0)
        panel  = params.get("interview_panel", ["Hiring Manager", "Tech Lead"])

        slot = "2026-04-01T10:00:00Z"   # deterministic for demo purposes

        self.logger.info("Interview scheduled for %s (score: %.1f)", name, score)

        return {
            "status":           "INTERVIEW_SCHEDULED",
            "candidate":        name,
            "email":            email,
            "interview_slot":   slot,
            "panel":            panel,
            "overall_score":    score,
            "message":          (
                f"Congratulations {name}! Your application scored {score}/100. "
                f"We'd like to invite you for an interview on {slot}."
            ),
        }


# ── 7. Rejection ──────────────────────────────────────────────────────────────

@register_agent("RejectionAgent")
class RejectionAgent(BaseAgent):
    """
    Generates a polite rejection for candidates below the threshold.

    Input params:
        candidate_name  (str)
        candidate_email (str)
        overall_score   (float)
        missing_skills  (list[str])  optional
    """

    async def run(self, params: Dict[str, Any]) -> Dict[str, Any]:
        name    = params.get("candidate_name", "Candidate")
        email   = params.get("candidate_email", "")
        score   = params.get("overall_score", 0)
        missing = params.get("missing_skills", [])

        self.logger.info("Rejection generated for %s (score: %.1f)", name, score)

        return {
            "status":        "REJECTED",
            "candidate":     name,
            "email":         email,
            "overall_score": score,
            "message":       (
                f"Thank you for applying, {name}. After careful review we will "
                f"not be moving forward at this time. "
                + (f"Key areas to strengthen: {', '.join(missing)}." if missing else "")
            ),
        }


# ── 8. Report Compiler ────────────────────────────────────────────────────────

@register_agent("ReportCompilerAgent")
class ReportCompilerAgent(BaseAgent):
    """
    Assembles the final screening report from all pipeline outputs.

    Input params:
        candidate_name   (str)
        overall_score    (float)
        decision         (str)   PROCEED | REJECT
        skill_score      (float)
        experience_score (float)
        culture_score    (float)
        outcome          (str)   final outcome message
    """

    async def run(self, params: Dict[str, Any]) -> Dict[str, Any]:
        name  = params.get("candidate_name", "Unknown")
        score = params.get("overall_score", 0)

        report = {
            "report_title":      f"Screening Report — {name}",
            "candidate":         name,
            "overall_score":     score,
            "decision":          params.get("decision", "UNKNOWN"),
            "score_breakdown": {
                "skills":      params.get("skill_score", 0),
                "experience":  params.get("experience_score", 0),
                "culture_fit": params.get("culture_score", 0),
            },
            "outcome_message":   params.get("outcome", ""),
            "recommendation":    (
                "Strong hire — fast-track to offer stage."
                if score >= 85 else
                "Hire — proceed with standard interview process."
                if score >= 70 else
                "No hire — does not meet minimum bar."
            ),
        }

        self.logger.info("Report compiled for %s: %s (%.1f)",
                         name, report["decision"], score)
        return {"report": report}
