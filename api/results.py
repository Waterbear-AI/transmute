"""Results API endpoint for the Results Panel.

Aggregates assessment state and profile snapshots for frontend display.
"""

import json
import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from agents.transmutation.tools import detect_check_in_regression, generate_comparison_snapshot
from api.auth import get_current_user_id
from db.database import get_db_session

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/results", tags=["results"])


class ProfileSnapshotResponse(BaseModel):
    id: str
    scores: Optional[dict[str, Any]] = None
    quadrant_placement: Optional[dict[str, Any]] = None
    quadrant: Optional[str] = None
    interpretation: Optional[str] = None
    has_spider_chart: bool = False
    spider_data: Optional[dict[str, Any]] = None
    flow_data: Optional[dict[str, Any]] = None
    created_at: Optional[str] = None


class AssessmentSummary(BaseModel):
    exists: bool
    answered: int = 0
    total: int = 0
    scenarios_completed: int = 0
    scenarios_total: int = 0
    current_phase: Optional[str] = None


class EducationProgressResponse(BaseModel):
    exists: bool
    progress: Optional[dict[str, Any]] = None
    summary: Optional[dict[str, Any]] = None


class DevelopmentResponse(BaseModel):
    has_roadmap: bool
    roadmap: Optional[dict[str, Any]] = None
    practice_count: int = 0
    roadmap_created_at: Optional[str] = None


class GraduationResponse(BaseModel):
    exists: bool
    pattern_narrative: Optional[str] = None
    graduation_indicators: Optional[dict[str, Any]] = None
    created_at: Optional[str] = None


class CheckInQuadrantDetail(BaseModel):
    baseline: str
    current: str
    downgraded: bool


class CheckInRegressedDimension(BaseModel):
    dimension: str
    baseline_normalized: float
    current_normalized: float
    drop_normalized: float


class CheckInRegressionDetail(BaseModel):
    evaluated: bool
    regression_detected: bool
    reason: str
    threshold_normalized: float
    regressed_dimensions: list[CheckInRegressedDimension]
    quadrant: CheckInQuadrantDetail
    baseline_snapshot_id: Optional[str] = None
    check_in_snapshot_id: Optional[str] = None


class CheckInComparisonDelta(BaseModel):
    previous: float
    current: float
    delta: float
    previous_normalized: float
    current_normalized: float
    delta_normalized: float
    direction: str  # "up" | "down" | "stable"


class CheckInQuadrantShift(BaseModel):
    previous: str
    current: str
    shifted: bool


class CheckInComparison(BaseModel):
    current_snapshot_id: str
    previous_snapshot_id: str
    deltas: dict[str, CheckInComparisonDelta]
    quadrant_shift: CheckInQuadrantShift
    current_created_at: Optional[str] = None
    previous_created_at: Optional[str] = None
    # flow_deltas intentionally omitted from v1 surface (out-of-scope per spec B14)


class CheckInResponse(BaseModel):
    count: int = 0
    latest_regression: Optional[bool] = None             # EXISTING — unchanged
    latest_created_at: Optional[str] = None              # EXISTING — unchanged
    latest_regression_detail: Optional[CheckInRegressionDetail] = None  # NEW
    latest_comparison: Optional[CheckInComparison] = None               # NEW


class ResultsResponse(BaseModel):
    user_id: str
    current_phase: Optional[str] = None
    assessment: AssessmentSummary
    profiles: list[ProfileSnapshotResponse]
    latest_profile: Optional[ProfileSnapshotResponse] = None
    education: Optional[EducationProgressResponse] = None
    development: Optional[DevelopmentResponse] = None
    graduation: Optional[GraduationResponse] = None
    check_ins: Optional[CheckInResponse] = None


@router.get("/{target_user_id}", response_model=ResultsResponse)
def get_results(
    target_user_id: str,
    user_id: str = Depends(get_current_user_id),
):
    """Return aggregated results for the Results Panel.

    Users can only access their own results.
    """
    if target_user_id != user_id:
        raise HTTPException(status_code=403, detail="Cannot access another user's results")

    with get_db_session() as conn:
        # Get user phase
        user_row = conn.execute(
            "SELECT current_phase FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        if not user_row:
            raise HTTPException(status_code=404, detail="User not found")

        current_phase = user_row["current_phase"]

        # Get assessment state
        assessment_row = conn.execute(
            "SELECT responses, scenario_responses, current_phase FROM assessment_state WHERE user_id = ? ORDER BY created_at DESC LIMIT 1",
            (user_id,),
        ).fetchone()

        if assessment_row:
            responses = json.loads(assessment_row["responses"] or "{}")
            scenario_responses = json.loads(assessment_row["scenario_responses"] or "{}")
            from agents.transmutation.question_bank import get_question_bank
            qb = get_question_bank()
            assessment = AssessmentSummary(
                exists=True,
                answered=len(responses),
                total=len(qb.get_all_questions()),
                scenarios_completed=len(scenario_responses),
                scenarios_total=len(qb.get_all_scenarios()),
                current_phase=assessment_row["current_phase"],
            )
        else:
            assessment = AssessmentSummary(exists=False)

        # Get profile snapshots
        profile_rows = conn.execute(
            "SELECT id, scores, quadrant_placement, interpretation, spider_chart, flow_data, created_at FROM profile_snapshots WHERE user_id = ? ORDER BY created_at DESC",
            (user_id,),
        ).fetchall()

        profiles = []
        for row in profile_rows:
            qp = json.loads(row["quadrant_placement"]) if row["quadrant_placement"] else None
            quadrant_name = qp.get("quadrant") if isinstance(qp, dict) else None

            spider_data = None
            if row["spider_chart"]:
                import base64
                spider_data = {"image_base64": base64.b64encode(row["spider_chart"]).decode("ascii")}

            flow_data = None
            if row["flow_data"]:
                try:
                    flow_data = json.loads(row["flow_data"]) if isinstance(row["flow_data"], str) else row["flow_data"]
                except Exception:
                    pass

            profiles.append(ProfileSnapshotResponse(
                id=row["id"],
                scores=json.loads(row["scores"]) if row["scores"] else None,
                quadrant_placement=qp,
                quadrant=quadrant_name,
                interpretation=row["interpretation"],
                has_spider_chart=row["spider_chart"] is not None,
                spider_data=spider_data,
                flow_data=flow_data,
                created_at=row["created_at"],
            ))

        # Get education progress
        edu_row = conn.execute(
            "SELECT progress FROM education_progress WHERE user_id = ?",
            (user_id,),
        ).fetchone()

        if edu_row:
            edu_progress = json.loads(edu_row["progress"] or "{}")
            total_cats = 0
            completed_cats = 0
            for dim, cats in edu_progress.items():
                for cat, data in cats.items():
                    total_cats += 1
                    if data.get("understanding_score", 0) >= 70:
                        completed_cats += 1
            education = EducationProgressResponse(
                exists=True,
                progress=edu_progress,
                summary={
                    "total_categories": total_cats,
                    "completed_categories": completed_cats,
                    "completion_pct": round(completed_cats / total_cats * 100, 1) if total_cats > 0 else 0,
                },
            )
        else:
            education = EducationProgressResponse(exists=False)

        # Get development data
        roadmap_row = conn.execute(
            "SELECT roadmap, created_at FROM development_roadmap WHERE user_id = ? ORDER BY created_at DESC LIMIT 1",
            (user_id,),
        ).fetchone()

        practice_count_row = conn.execute(
            "SELECT COUNT(*) as cnt FROM practice_journal WHERE user_id = ?",
            (user_id,),
        ).fetchone()

        development = DevelopmentResponse(
            has_roadmap=roadmap_row is not None,
            roadmap=json.loads(roadmap_row["roadmap"]) if roadmap_row else None,
            practice_count=practice_count_row["cnt"] if practice_count_row else 0,
            roadmap_created_at=roadmap_row["created_at"] if roadmap_row else None,
        )

        # Get graduation record
        grad_row = conn.execute(
            "SELECT final_snapshot_id, pattern_narrative, graduation_indicators, created_at FROM graduation_record WHERE user_id = ? ORDER BY created_at DESC LIMIT 1",
            (user_id,),
        ).fetchone()

        graduation = GraduationResponse(
            exists=grad_row is not None,
            pattern_narrative=grad_row["pattern_narrative"] if grad_row else None,
            graduation_indicators=json.loads(grad_row["graduation_indicators"]) if grad_row else None,
            created_at=grad_row["created_at"] if grad_row else None,
        )

        # Get check-in logs
        checkin_count = conn.execute(
            "SELECT COUNT(*) as cnt FROM check_in_log WHERE user_id = ?",
            (user_id,),
        ).fetchone()

        latest_checkin = conn.execute(
            "SELECT regression_detected, created_at FROM check_in_log WHERE user_id = ? ORDER BY created_at DESC LIMIT 1",
            (user_id,),
        ).fetchone()

        check_ins = CheckInResponse(
            count=checkin_count["cnt"] if checkin_count else 0,
            latest_regression=latest_checkin["regression_detected"] if latest_checkin else None,
            latest_created_at=latest_checkin["created_at"] if latest_checkin else None,
        )

        # Capture for use outside the db session context
        final_snapshot_id = grad_row["final_snapshot_id"] if grad_row else None
        latest_profile_id = profiles[0].id if profiles else None

    # Recompute regression detail when at least one check-in exists (outside db session)
    if check_ins.count > 0:
        try:
            verdict = detect_check_in_regression(user_id)
            regressed_dims = [
                CheckInRegressedDimension(**d)
                for d in (verdict.get("regressed_dimensions") or [])
            ]
            quadrant_raw = verdict.get("quadrant") or {}
            check_ins.latest_regression_detail = CheckInRegressionDetail(
                evaluated=verdict["evaluated"],
                regression_detected=verdict["regression_detected"],
                reason=verdict.get("reason", ""),
                threshold_normalized=verdict.get("threshold_normalized", 0.0),
                regressed_dimensions=regressed_dims,
                quadrant=CheckInQuadrantDetail(
                    baseline=quadrant_raw.get("baseline", ""),
                    current=quadrant_raw.get("current", ""),
                    downgraded=quadrant_raw.get("downgraded", False),
                ),
                baseline_snapshot_id=verdict.get("baseline_snapshot_id"),
                check_in_snapshot_id=verdict.get("check_in_snapshot_id"),
            )
        except Exception as exc:
            logger.warning(
                "check-in regression detail recompute failed for user %s: %s",
                user_id, exc, exc_info=True,
            )
            check_ins.latest_regression_detail = None

    # Recompute comparison snapshot when graduation baseline exists and latest snapshot differs
    if final_snapshot_id and latest_profile_id and latest_profile_id != final_snapshot_id:
        try:
            comp = generate_comparison_snapshot(user_id, final_snapshot_id)
            deltas_raw = comp.get("deltas") or {}
            deltas = {
                dim: CheckInComparisonDelta(**delta_data)
                for dim, delta_data in deltas_raw.items()
            }
            qs_raw = comp.get("quadrant_shift") or {}
            check_ins.latest_comparison = CheckInComparison(
                current_snapshot_id=comp["current_snapshot_id"],
                previous_snapshot_id=comp["previous_snapshot_id"],
                deltas=deltas,
                quadrant_shift=CheckInQuadrantShift(
                    previous=qs_raw.get("previous", ""),
                    current=qs_raw.get("current", ""),
                    shifted=qs_raw.get("shifted", False),
                ),
                current_created_at=comp.get("current_created_at"),
                previous_created_at=comp.get("previous_created_at"),
            )
        except Exception as exc:
            logger.warning(
                "check-in comparison snapshot recompute failed for user %s: %s",
                user_id, exc, exc_info=True,
            )
            check_ins.latest_comparison = None

    return ResultsResponse(
        user_id=user_id,
        current_phase=current_phase,
        assessment=assessment,
        profiles=profiles,
        latest_profile=profiles[0] if profiles else None,
        education=education,
        development=development,
        graduation=graduation,
        check_ins=check_ins,
    )
