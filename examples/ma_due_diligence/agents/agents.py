"""
M&A Due Diligence Specialist Agents
====================================

Each agent calls the OpenAI API with a focused system prompt and returns
a structured analysis dict with a 'confidence' score so the graph engine's
reflection loops can auto-trigger critique nodes when quality is low.

Registered agents:
  DataIngestionAgent          Parses and normalises raw company data
  FinancialAnalystAgent       P&L, balance sheet, DCF, EBITDA analysis
  LegalDueDiligenceAgent      Contracts, IP, litigation, regulatory exposure
  TechnicalDueDiligenceAgent  Tech stack, scalability, engineering quality
  MarketAnalysisAgent         TAM/SAM, competitive landscape, growth thesis
  CultureFitAgent             Leadership, employee sentiment, values alignment
  RiskSynthesizerAgent        Consolidates signals from all expert agents
  ValuationAgent              DCF + comparable company valuation, premium range
  ComplianceAgent             Antitrust, CFIUS, regulatory approval path
  CritiqueAgent               Generic quality critic for reflection loops
  ExecutiveSummaryAgent       Final board-ready recommendation
  FallbackComplianceAgent     Safe fallback when ComplianceAgent circuit trips
"""
from __future__ import annotations

import json
import logging
import os
import sys
from typing import Any, Dict

from openai import AsyncOpenAI

from agents.base_agent import BaseAgent
from orchestrator.services.agent_registry import register_agent

# Ensure project root is on path when imported from any working directory
_repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

logger = logging.getLogger(__name__)

_LLM_MODEL = os.getenv("LLM_MODEL", "gpt-4o")
_API_KEY = os.getenv("OPENAI_API_KEY", "")


async def _llm(system: str, user: str, temperature: float = 0.2) -> Dict[str, Any]:
    """
    Call the OpenAI chat API and parse the response as JSON.
    Falls back to a structured placeholder when no API key is configured
    so the example can be run offline for demonstration.
    """
    if not _API_KEY:
        # Offline demo mode — return realistic structured placeholder
        return {
            "result": f"[DEMO] {system[:60]}...",
            "confidence": 0.82,
            "status": "demo_mode",
            "note": "Set OPENAI_API_KEY for real analysis.",
        }

    client = AsyncOpenAI(api_key=_API_KEY)
    response = await client.chat.completions.create(
        model=_LLM_MODEL,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        temperature=temperature,
        response_format={"type": "json_object"},
    )
    text = response.choices[0].message.content or "{}"
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"raw": text, "confidence": 0.5}


# ── Data Ingestion ─────────────────────────────────────────────────────────────

@register_agent("DataIngestionAgent")
class DataIngestionAgent(BaseAgent):
    """
    Normalises raw company profile data into a canonical structure.
    Extracts: company name, sector, founding year, employee count,
    revenue history, key products, subsidiaries, and jurisdiction.
    """

    async def run(self, params: Dict[str, Any]) -> Dict[str, Any]:
        company = params.get("company_name", "Target Co")
        raw_data = params.get("raw_data", {})

        result = await _llm(
            system=(
                "You are a data normalisation specialist for M&A due diligence. "
                "Extract and normalise company profile data. "
                "Return JSON: {company_name, sector, founded_year, employee_count, "
                "hq_jurisdiction, revenue_usd_m (last 3 years list), key_products, "
                "subsidiaries, data_quality_issues, confidence}"
            ),
            user=(
                f"Company: {company}\n"
                f"Raw data provided: {json.dumps(raw_data, default=str)}\n"
                "Normalise and flag any data quality issues."
            ),
        )
        return {**result, "agent": "DataIngestionAgent", "company": company}


# ── Financial Analysis ─────────────────────────────────────────────────────────

@register_agent("FinancialAnalystAgent")
class FinancialAnalystAgent(BaseAgent):
    """
    Deep financial analysis: revenue growth, margins, EBITDA, cash conversion,
    working capital, debt structure, and 5-year DCF projection.
    """

    async def run(self, params: Dict[str, Any]) -> Dict[str, Any]:
        company = params.get("company_name", "Target Co")
        ingested = params.get("ingested_profile", {})

        result = await _llm(
            system=(
                "You are a senior M&A financial analyst at a top investment bank. "
                "Perform comprehensive financial due diligence. "
                "Return JSON: {revenue_cagr_3yr, ebitda_margin, gross_margin, "
                "net_debt_to_ebitda, free_cash_flow_yield, dcf_valuation_usd_m, "
                "key_financial_risks, financial_quality_score (0-10), "
                "red_flags, confidence (0.0-1.0)}"
            ),
            user=(
                f"Company: {company}\n"
                f"Profile data: {json.dumps(ingested, default=str)}\n"
                "Provide rigorous financial due diligence."
            ),
        )
        return {**result, "agent": "FinancialAnalystAgent"}


# ── Legal Due Diligence ────────────────────────────────────────────────────────

@register_agent("LegalDueDiligenceAgent")
class LegalDueDiligenceAgent(BaseAgent):
    """
    Reviews material contracts, IP portfolio, pending litigation,
    employment agreements, and regulatory licences.
    """

    async def run(self, params: Dict[str, Any]) -> Dict[str, Any]:
        company = params.get("company_name", "Target Co")
        profile = params.get("ingested_profile", {})

        result = await _llm(
            system=(
                "You are a senior M&A legal counsel specialising in corporate transactions. "
                "Assess legal risks for an acquisition. "
                "Return JSON: {ip_ownership_clean (bool), material_litigation_exposure_usd_m, "
                "change_of_control_clauses_count, regulatory_licences_transferable (bool), "
                "employment_liability_usd_m, legal_risk_rating (low/medium/high/critical), "
                "blocking_issues, confidence (0.0-1.0)}"
            ),
            user=(
                f"Company: {company}\n"
                f"Profile: {json.dumps(profile, default=str)}\n"
                "Identify all legal risks and blocking issues."
            ),
        )
        return {**result, "agent": "LegalDueDiligenceAgent"}


# ── Technical Due Diligence ────────────────────────────────────────────────────

@register_agent("TechnicalDueDiligenceAgent")
class TechnicalDueDiligenceAgent(BaseAgent):
    """
    Evaluates technology stack, scalability, technical debt, security posture,
    engineering team quality, and integration complexity.
    """

    async def run(self, params: Dict[str, Any]) -> Dict[str, Any]:
        company = params.get("company_name", "Target Co")
        profile = params.get("ingested_profile", {})

        result = await _llm(
            system=(
                "You are a CTO-level technical due diligence expert. "
                "Assess the technical health of an acquisition target. "
                "Return JSON: {tech_stack_modernity_score (0-10), scalability_rating (0-10), "
                "technical_debt_severity (low/medium/high/critical), "
                "security_posture_score (0-10), engineering_team_quality (0-10), "
                "integration_complexity (low/medium/high), estimated_remediation_cost_usd_m, "
                "key_technical_risks, confidence (0.0-1.0)}"
            ),
            user=(
                f"Company: {company}\n"
                f"Profile: {json.dumps(profile, default=str)}\n"
                "Provide a complete technical due diligence assessment."
            ),
        )
        return {**result, "agent": "TechnicalDueDiligenceAgent"}


# ── Market Analysis ────────────────────────────────────────────────────────────

@register_agent("MarketAnalysisAgent")
class MarketAnalysisAgent(BaseAgent):
    """
    Analyses total addressable market, competitive dynamics, market share,
    growth trajectory, and acquisition strategic rationale.
    """

    async def run(self, params: Dict[str, Any]) -> Dict[str, Any]:
        company = params.get("company_name", "Target Co")
        profile = params.get("ingested_profile", {})
        variant = params.get("analysis_variant", "standard")  # for fan-out differentiation

        result = await _llm(
            system=(
                f"You are a strategy consultant specialising in market analysis (variant: {variant}). "
                "Assess the market position and strategic value of an M&A target. "
                "Return JSON: {tam_usd_b, sam_usd_b, market_share_pct, "
                "market_growth_cagr_5yr, competitive_moat (none/weak/moderate/strong), "
                "top_3_competitors, strategic_fit_score (0-10), "
                "market_risk_factors, confidence (0.0-1.0)}"
            ),
            user=(
                f"Company: {company}\nVariant: {variant}\n"
                f"Profile: {json.dumps(profile, default=str)}\n"
                "Provide strategic market analysis."
            ),
        )
        return {**result, "agent": "MarketAnalysisAgent", "variant": variant}


# ── Culture & People ───────────────────────────────────────────────────────────

@register_agent("CultureFitAgent")
class CultureFitAgent(BaseAgent):
    """
    Assesses leadership team quality, cultural alignment, employee retention
    risk, and talent concentration risk.
    """

    async def run(self, params: Dict[str, Any]) -> Dict[str, Any]:
        company = params.get("company_name", "Target Co")
        profile = params.get("ingested_profile", {})
        acquirer = params.get("acquirer_name", "Acquirer Corp")

        result = await _llm(
            system=(
                "You are an HR and organisational psychologist specialising in M&A integration. "
                "Assess people and culture risks for an acquisition. "
                "Return JSON: {leadership_quality_score (0-10), "
                "cultural_alignment_score (0-10, between target and acquirer), "
                "key_person_dependency_count, estimated_attrition_post_close_pct, "
                "integration_difficulty (low/medium/high), "
                "retention_program_cost_usd_m, culture_blockers, confidence (0.0-1.0)}"
            ),
            user=(
                f"Target: {company}, Acquirer: {acquirer}\n"
                f"Profile: {json.dumps(profile, default=str)}\n"
                "Assess cultural fit and people risks."
            ),
        )
        return {**result, "agent": "CultureFitAgent"}


# ── Risk Synthesis ─────────────────────────────────────────────────────────────

@register_agent("RiskSynthesizerAgent")
class RiskSynthesizerAgent(BaseAgent):
    """
    Aggregates all expert agent outputs into a unified risk register
    with severity scoring and deal-breaker identification.
    """

    async def run(self, params: Dict[str, Any]) -> Dict[str, Any]:
        company = params.get("company_name", "Target Co")
        financial = params.get("financial_analysis", {})
        legal = params.get("legal_analysis", {})
        technical = params.get("technical_analysis", {})
        market = params.get("market_analysis", {})
        culture = params.get("culture_analysis", {})

        result = await _llm(
            system=(
                "You are a Chief Risk Officer specialising in M&A transactions. "
                "Synthesise all due diligence streams into a unified risk assessment. "
                "Return JSON: {overall_risk_rating (low/medium/high/critical), "
                "deal_breakers (list of strings or empty), "
                "top_5_risks (list of {risk, severity, mitigation}), "
                "total_risk_adjusted_cost_usd_m, "
                "recommendation_to_proceed (bool), "
                "conditions_to_proceed (list), confidence (0.0-1.0)}"
            ),
            user=(
                f"Company: {company}\n"
                f"Financial: {json.dumps(financial, default=str)}\n"
                f"Legal: {json.dumps(legal, default=str)}\n"
                f"Technical: {json.dumps(technical, default=str)}\n"
                f"Market: {json.dumps(market, default=str)}\n"
                f"Culture: {json.dumps(culture, default=str)}\n"
                "Synthesise into a master risk register."
            ),
            temperature=0.1,
        )
        return {**result, "agent": "RiskSynthesizerAgent"}


# ── Valuation ──────────────────────────────────────────────────────────────────

@register_agent("ValuationAgent")
class ValuationAgent(BaseAgent):
    """
    Multi-method valuation: DCF, comparable company analysis (CCA),
    precedent transactions, and LBO analysis.  Returns valuation range
    and recommended acquisition premium.
    """

    async def run(self, params: Dict[str, Any]) -> Dict[str, Any]:
        company = params.get("company_name", "Target Co")
        financial = params.get("financial_analysis", {})
        market = params.get("market_analysis", {})
        risk = params.get("risk_synthesis", {})

        result = await _llm(
            system=(
                "You are a Managing Director in M&A investment banking. "
                "Provide a rigorous multi-methodology valuation. "
                "Return JSON: {dcf_value_usd_m, cca_value_usd_m, "
                "precedent_tx_value_usd_m, lbo_max_bid_usd_m, "
                "recommended_bid_range_usd_m (list of [low, high]), "
                "recommended_premium_pct, ev_to_ebitda_multiple, "
                "valuation_rationale, confidence (0.0-1.0)}"
            ),
            user=(
                f"Company: {company}\n"
                f"Financial analysis: {json.dumps(financial, default=str)}\n"
                f"Market analysis: {json.dumps(market, default=str)}\n"
                f"Risk synthesis: {json.dumps(risk, default=str)}\n"
                "Provide a full valuation with all four methods."
            ),
            temperature=0.1,
        )
        return {**result, "agent": "ValuationAgent"}


# ── Compliance Check ───────────────────────────────────────────────────────────

@register_agent("ComplianceAgent")
class ComplianceAgent(BaseAgent):
    """
    Assesses antitrust exposure (HSR, EU merger control), CFIUS/FDI screening,
    sector-specific regulatory approvals, and estimated timeline.
    """

    async def run(self, params: Dict[str, Any]) -> Dict[str, Any]:
        company = params.get("company_name", "Target Co")
        acquirer = params.get("acquirer_name", "Acquirer Corp")
        profile = params.get("ingested_profile", {})
        market = params.get("market_analysis", {})

        result = await _llm(
            system=(
                "You are a regulatory affairs partner at a global law firm. "
                "Assess merger control and regulatory clearance requirements. "
                "Return JSON: {hsr_filing_required (bool), eu_phase_1_likely (bool), "
                "eu_phase_2_risk (bool), cfius_review_required (bool), "
                "sector_specific_approvals (list), estimated_clearance_months, "
                "remedies_likely_required (bool), regulatory_risk_rating (low/medium/high), "
                "confidence (0.0-1.0)}"
            ),
            user=(
                f"Target: {company}, Acquirer: {acquirer}\n"
                f"Profile: {json.dumps(profile, default=str)}\n"
                f"Market position: {json.dumps(market, default=str)}\n"
                "Assess all regulatory requirements."
            ),
        )
        return {**result, "agent": "ComplianceAgent"}


@register_agent("FallbackComplianceAgent")
class FallbackComplianceAgent(BaseAgent):
    """
    Conservative compliance fallback — returns a safe assessment when
    ComplianceAgent is unavailable (circuit breaker tripped).
    """

    async def run(self, params: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "agent": "FallbackComplianceAgent",
            "note": "Primary ComplianceAgent unavailable — conservative fallback applied.",
            "hsr_filing_required": True,
            "eu_phase_1_likely": True,
            "eu_phase_2_risk": True,
            "cfius_review_required": True,
            "estimated_clearance_months": 18,
            "remedies_likely_required": True,
            "regulatory_risk_rating": "high",
            "confidence": 0.6,
        }


# ── Critique (Reflection) ──────────────────────────────────────────────────────

@register_agent("CritiqueAgent")
class CritiqueAgent(BaseAgent):
    """
    Generic quality critic used in reflection loops.
    Reviews prior agent output, identifies gaps, and returns an improved
    version with a new confidence score.
    """

    async def run(self, params: Dict[str, Any]) -> Dict[str, Any]:
        subject = params.get("subject", {})
        instruction = params.get("instruction", "Critique and improve the analysis.")
        reflection_round = params.get("reflection_round", 1)

        result = await _llm(
            system=(
                "You are a senior quality reviewer for M&A due diligence. "
                "Critically review the provided analysis for gaps, errors, or low confidence areas. "
                "Return JSON: {critique (string), identified_gaps (list), "
                "improved_output (object with same structure as input but improved), "
                "confidence (0.0-1.0), reflection_round}"
            ),
            user=(
                f"Reflection round: {reflection_round}\n"
                f"Instruction: {instruction}\n"
                f"Subject analysis to critique:\n{json.dumps(subject, default=str, indent=2)}\n"
                "Improve the analysis and return a higher-confidence version."
            ),
            temperature=0.1,
        )
        return {**result, "agent": "CritiqueAgent", "reflection_round": reflection_round}


# ── Executive Summary ──────────────────────────────────────────────────────────

@register_agent("ExecutiveSummaryAgent")
class ExecutiveSummaryAgent(BaseAgent):
    """
    Generates a board-ready executive summary: deal rationale, headline numbers,
    key risks, recommended bid range, and go/no-go recommendation.
    """

    async def run(self, params: Dict[str, Any]) -> Dict[str, Any]:
        company = params.get("company_name", "Target Co")
        acquirer = params.get("acquirer_name", "Acquirer Corp")
        risk = params.get("risk_synthesis", {})
        valuation = params.get("valuation", {})
        compliance = params.get("compliance", {})
        financial = params.get("financial_analysis", {})

        result = await _llm(
            system=(
                "You are a Managing Director preparing a board memo for an acquisition decision. "
                "Write a concise, decisive executive summary. "
                "Return JSON: {headline_recommendation (PROCEED / PROCEED_WITH_CONDITIONS / DO_NOT_PROCEED), "
                "deal_rationale (string, 3 sentences), "
                "headline_numbers: {bid_range_usd_m, ev_ebitda, expected_irr_pct, payback_years}, "
                "top_3_value_creation_levers (list), "
                "top_3_deal_risks (list), "
                "conditions_for_proceeding (list), "
                "next_steps (list of immediate actions), "
                "confidence (0.0-1.0)}"
            ),
            user=(
                f"Acquisition of: {company} by {acquirer}\n"
                f"Risk synthesis: {json.dumps(risk, default=str)}\n"
                f"Valuation: {json.dumps(valuation, default=str)}\n"
                f"Compliance: {json.dumps(compliance, default=str)}\n"
                f"Financial summary: {json.dumps(financial, default=str)}\n"
                "Write the board recommendation memo."
            ),
            temperature=0.1,
        )
        return {**result, "agent": "ExecutiveSummaryAgent"}
