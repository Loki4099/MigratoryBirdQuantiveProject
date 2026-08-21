from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import Engine, text

from style_rotation.v022.data_seed_import import (
    ExternalImportManifestPublication,
    ExternalImportManifestService,
    ExternalImportManifestSpec,
    ExternalImportObjectSpec,
)
from style_rotation.v022.security_lifecycle import (
    LifecycleEvidenceRef,
    SecurityLifecycleEventPublication,
    SecurityLifecycleEventService,
    SecurityLifecycleEventSpec,
    SecuritySettlementLegSpec,
)


@dataclass(frozen=True, slots=True)
class FrozenSp500LifecyclePublication:
    evidence: ExternalImportManifestPublication
    events: tuple[SecurityLifecycleEventPublication, ...]


def official_lifecycle_evidence_spec(*, created_by: str) -> ExternalImportManifestSpec:
    """Exact official records used by the first frozen lifecycle repair set."""

    return ExternalImportManifestSpec(
        manifest_key="sp500_official_lifecycle_evidence_v1",
        version_number=1,
        source_project_key="migratory_bird_v022_sp500_data_governance",
        source_release_key="official_completion_records_v1",
        created_by=created_by,
        objects=(
            ExternalImportObjectSpec(
                object_role="corporate_action_terms",
                logical_key="scg_dominion_completion_2019",
                media_type="application/pdf",
                content_sha256=(
                    "65b7a9e1380437b12ac3c76b89dec0e3dbe9b9888d7db8339c2022322df54766"
                ),
                size_bytes=163926,
                source_uri=(
                    "https://d18rn0p25nwr6d.cloudfront.net/CIK-0000754737/"
                    "11a832ae-c7e5-414d-9c8f-c090d139374f.pdf"
                ),
                license_key="public_company_filing",
                provider_key="dominion_investor_relations",
                provenance_status="verified",
                usage_scope="local_research",
                metadata={
                    "source_security_key": "scg",
                    "successor_security_key": "d",
                    "completion_date": "2019-01-01",
                    "exchange_ratio": "0.6690",
                },
            ),
            ExternalImportObjectSpec(
                object_role="corporate_action_terms",
                logical_key="tss_global_payments_completion_2019",
                media_type="text/html",
                content_sha256=(
                    "d6b58c8443bbb280d013b04e9003be1b12d75411ce0d4d8f43a2de1b23b20a9d"
                ),
                size_bytes=76051,
                source_uri=(
                    "https://investors.globalpayments.com/financial-information/"
                    "all-sec-filings/content/0001193125-19-250768/d801793d8k.htm"
                ),
                license_key="public_company_filing",
                provider_key="global_payments_investor_relations",
                provenance_status="verified",
                usage_scope="local_research",
                metadata={
                    "source_security_key": "tss",
                    "successor_security_key": "gpn",
                    "completion_date": "2019-09-18",
                    "exchange_ratio": "0.8101",
                },
            ),
            ExternalImportObjectSpec(
                object_role="corporate_action_terms",
                logical_key="abmd_jnj_completion_2022",
                media_type="application/pdf",
                content_sha256=(
                    "434983cb1d9434263822613ac2e5edccf78aa0cab43346f3cb19c25dd8d6b3c1"
                ),
                size_bytes=1236455,
                source_uri=(
                    "https://www.investor.jnj.com/files/doc_financials/2023/q2/"
                    "0000200406-23-000082.pdf"
                ),
                license_key="public_company_filing",
                provider_key="johnson_and_johnson_investor_relations",
                provenance_status="verified",
                usage_scope="local_research",
                metadata={
                    "source_security_key": "abmd",
                    "completion_date": "2022-12-22",
                    "cash_per_share_usd": "380.00",
                    "nontradeable_cvr_excluded": True,
                },
            ),
        ),
    )


def official_l3_lifecycle_evidence_spec(*, created_by: str) -> ExternalImportManifestSpec:
    """Official completion record for the L3/Harris stock merger."""

    return ExternalImportManifestSpec(
        manifest_key="sp500_official_l3_lifecycle_evidence_v1",
        version_number=1,
        source_project_key="migratory_bird_v022_sp500_data_governance",
        source_release_key="official_l3harris_completion_record_v1",
        created_by=created_by,
        objects=(
            ExternalImportObjectSpec(
                object_role="corporate_action_terms",
                logical_key="lll_l3harris_completion_2019",
                media_type="application/pdf",
                content_sha256=(
                    "9eceaa1511cb54481bdb0bc5ab4d97bb3b19b9c908fb89a574e932367cfde4a7"
                ),
                size_bytes=105603,
                source_uri=(
                    "https://s205.q4cdn.com/565046976/files/doc_news/"
                    "L3Harris-Technologies-Merger-Successfully-Completed-Board-of-"
                    "Directors-Leadership-and-Organization-Structure-Announced-"
                    "07-01-2019-2019.pdf"
                ),
                license_key="public_company_press_release",
                provider_key="l3harris_investor_relations",
                provenance_status="verified",
                usage_scope="local_research",
                metadata={
                    "source_security_key": "lll",
                    "successor_security_key": "lhx",
                    "completion_date": "2019-06-29",
                    "last_trading_session": "2019-06-28",
                    "exchange_ratio": "1.30",
                },
            ),
        ),
    )


def official_aet_lifecycle_evidence_spec(*, created_by: str) -> ExternalImportManifestSpec:
    """Official CVS 8-K proving the completed Aetna cash-and-stock merger."""

    return ExternalImportManifestSpec(
        manifest_key="sp500_official_aet_lifecycle_evidence_v1",
        version_number=1,
        source_project_key="migratory_bird_v022_sp500_data_governance",
        source_release_key="official_aet_cvs_completion_record_v1",
        created_by=created_by,
        objects=(
            ExternalImportObjectSpec(
                object_role="corporate_action_terms",
                logical_key="aet_cvs_completion_2018",
                media_type="text/html",
                content_sha256=(
                    "7c70f17c710b62228abcb56f51c257dae81bd5a220e9bf7b1cc59a14b2fc03b7"
                ),
                size_bytes=31065,
                source_uri=(
                    "https://www.sec.gov/Archives/edgar/data/64803/"
                    "000119312518336591/d650950d8k.htm"
                ),
                license_key="public_company_filing",
                provider_key="sec_edgar",
                provenance_status="verified",
                usage_scope="local_research",
                metadata={
                    "source_security_key": "aet",
                    "successor_security_key": "cvs",
                    "completion_date": "2018-11-28",
                    "last_trading_session": "2018-11-28",
                    "cash_per_share_usd": "145.00",
                    "exchange_ratio": "0.8378",
                    "sec_accession": "0001193125-18-336591",
                },
            ),
        ),
    )


def official_esrx_lifecycle_evidence_spec(*, created_by: str) -> ExternalImportManifestSpec:
    """Official Cigna 8-K proving the completed Express Scripts merger."""

    return ExternalImportManifestSpec(
        manifest_key="sp500_official_esrx_lifecycle_evidence_v1",
        version_number=1,
        source_project_key="migratory_bird_v022_sp500_data_governance",
        source_release_key="official_esrx_cigna_completion_record_v1",
        created_by=created_by,
        objects=(
            ExternalImportObjectSpec(
                object_role="corporate_action_terms",
                logical_key="esrx_cigna_completion_2018",
                media_type="text/html",
                content_sha256=(
                    "2116bc2ee8a5395fc3570923e223180c4042c53101367821c25dde12c0009b26"
                ),
                size_bytes=57244,
                source_uri=(
                    "https://www.sec.gov/Archives/edgar/data/701221/"
                    "000114036118045478/form8k.htm"
                ),
                license_key="public_company_filing",
                provider_key="sec_edgar",
                provenance_status="verified",
                usage_scope="local_research",
                metadata={
                    "source_security_key": "esrx",
                    "successor_security_key": "ci",
                    "completion_date": "2018-12-20",
                    "last_trading_session": "2018-12-19",
                    "cash_per_share_usd": "48.75",
                    "exchange_ratio": "0.2434",
                    "sec_accession": "0001140361-18-045478",
                },
            ),
        ),
    )


def official_twx_lifecycle_evidence_spec(*, created_by: str) -> ExternalImportManifestSpec:
    """Official Time Warner 8-K proving the completed AT&T cash-and-stock merger."""

    return ExternalImportManifestSpec(
        manifest_key="sp500_official_twx_lifecycle_evidence_v1",
        version_number=1,
        source_project_key="migratory_bird_v022_sp500_data_governance",
        source_release_key="official_twx_att_completion_record_v1",
        created_by=created_by,
        objects=(
            ExternalImportObjectSpec(
                object_role="corporate_action_terms",
                logical_key="twx_att_completion_2018",
                media_type="text/html",
                content_sha256=(
                    "0a1e0210253d111e84c744449a5fdcb5bc58de7ecef4456fd36b5bcaa58ca28c"
                ),
                size_bytes=53864,
                source_uri=(
                    "https://www.sec.gov/Archives/edgar/data/1105705/"
                    "000095015718000694/form8-k.htm"
                ),
                license_key="public_company_filing",
                provider_key="sec_edgar",
                provenance_status="verified",
                usage_scope="local_research",
                metadata={
                    "source_security_key": "twx",
                    "successor_security_key": "t",
                    "agreement_date": "2016-10-22",
                    "prior_sec_disclosure_date": "2016-10-24",
                    "completion_date": "2018-06-14",
                    "last_trading_session": "2018-06-14",
                    "cash_per_share_usd": "53.75",
                    "exchange_ratio": "1.437",
                    "sec_accession": "0000950157-18-000694",
                    "trading_suspension": "prior_to_market_open_2018-06-15",
                },
            ),
        ),
    )


def frozen_l3_lifecycle_event_spec(
    security_ids: dict[str, uuid.UUID],
    *,
    evidence_artifact_id: uuid.UUID,
    created_by: str,
) -> SecurityLifecycleEventSpec:
    return SecurityLifecycleEventSpec(
        security_id=security_ids["lll"],
        event_key="lll_l3harris_stock_merger_2019",
        version_number=1,
        event_type="stock_merger",
        event_status="confirmed",
        announced_at=datetime(2018, 10, 14, 12, tzinfo=UTC),
        effective_session=date(2019, 6, 29),
        last_trading_session=date(2019, 6, 28),
        settlement_session=date(2019, 7, 1),
        selectable_after=False,
        tradable_after=False,
        valuation_state_after="terminal",
        evidence=(
            LifecycleEvidenceRef(evidence_artifact_id, "corporate_action_terms"),
        ),
        settlement_legs=(
            SecuritySettlementLegSpec(
                leg_kind="successor_security",
                target_security_id=security_ids["lhx"],
                quantity_per_source_share=Decimal("1.30"),
                valuation_policy="successor_market_value",
            ),
        ),
        created_by=created_by,
        details={"evidence_object": "lll_l3harris_completion_2019"},
    )


def frozen_aet_lifecycle_event_spec(
    security_ids: dict[str, uuid.UUID],
    *,
    evidence_artifact_id: uuid.UUID,
    created_by: str,
) -> SecurityLifecycleEventSpec:
    return SecurityLifecycleEventSpec(
        security_id=security_ids["aet"],
        event_key="aet_cvs_cash_stock_merger_2018",
        version_number=1,
        event_type="stock_merger",
        event_status="confirmed",
        announced_at=datetime(2018, 11, 28, 21, tzinfo=UTC),
        effective_session=date(2018, 11, 28),
        last_trading_session=date(2018, 11, 28),
        settlement_session=date(2018, 11, 28),
        selectable_after=False,
        tradable_after=False,
        valuation_state_after="terminal",
        evidence=(
            LifecycleEvidenceRef(evidence_artifact_id, "corporate_action_terms"),
        ),
        settlement_legs=(
            SecuritySettlementLegSpec(
                leg_kind="cash",
                cash_amount_per_source_share=Decimal("145.00"),
                currency="USD",
                valuation_policy="fixed_cash",
            ),
            SecuritySettlementLegSpec(
                leg_kind="successor_security",
                target_security_id=security_ids["cvs"],
                quantity_per_source_share=Decimal("0.8378"),
                valuation_policy="successor_market_value",
            ),
        ),
        created_by=created_by,
        details={
            "evidence_object": "aet_cvs_completion_2018",
            "sec_accession": "0001193125-18-336591",
        },
    )


def frozen_esrx_lifecycle_event_spec(
    security_ids: dict[str, uuid.UUID],
    *,
    evidence_artifact_id: uuid.UUID,
    created_by: str,
) -> SecurityLifecycleEventSpec:
    return SecurityLifecycleEventSpec(
        security_id=security_ids["esrx"],
        event_key="esrx_cigna_cash_stock_merger_2018",
        version_number=1,
        event_type="stock_merger",
        event_status="confirmed",
        announced_at=datetime(2018, 12, 20, 12, tzinfo=UTC),
        effective_session=date(2018, 12, 20),
        last_trading_session=date(2018, 12, 19),
        settlement_session=date(2018, 12, 20),
        selectable_after=False,
        tradable_after=False,
        valuation_state_after="terminal",
        evidence=(
            LifecycleEvidenceRef(evidence_artifact_id, "corporate_action_terms"),
        ),
        settlement_legs=(
            SecuritySettlementLegSpec(
                leg_kind="cash",
                cash_amount_per_source_share=Decimal("48.75"),
                currency="USD",
                valuation_policy="fixed_cash",
            ),
            SecuritySettlementLegSpec(
                leg_kind="successor_security",
                target_security_id=security_ids["ci"],
                quantity_per_source_share=Decimal("0.2434"),
                valuation_policy="successor_market_value",
            ),
        ),
        created_by=created_by,
        details={
            "evidence_object": "esrx_cigna_completion_2018",
            "sec_accession": "0001140361-18-045478",
        },
    )


def frozen_twx_lifecycle_event_spec(
    security_ids: dict[str, uuid.UUID],
    *,
    evidence_artifact_id: uuid.UUID,
    created_by: str,
) -> SecurityLifecycleEventSpec:
    return SecurityLifecycleEventSpec(
        security_id=security_ids["twx"],
        event_key="twx_att_cash_stock_merger_2018",
        version_number=1,
        event_type="stock_merger",
        event_status="confirmed",
        announced_at=datetime(2016, 10, 24, tzinfo=UTC),
        effective_session=date(2018, 6, 14),
        last_trading_session=date(2018, 6, 14),
        settlement_session=date(2018, 6, 14),
        selectable_after=False,
        tradable_after=False,
        valuation_state_after="terminal",
        evidence=(
            LifecycleEvidenceRef(evidence_artifact_id, "corporate_action_terms"),
        ),
        settlement_legs=(
            SecuritySettlementLegSpec(
                leg_kind="cash",
                cash_amount_per_source_share=Decimal("53.75"),
                currency="USD",
                valuation_policy="fixed_cash",
            ),
            SecuritySettlementLegSpec(
                leg_kind="successor_security",
                target_security_id=security_ids["t"],
                quantity_per_source_share=Decimal("1.437"),
                valuation_policy="successor_market_value",
            ),
        ),
        created_by=created_by,
        details={
            "evidence_object": "twx_att_completion_2018",
            "sec_accession": "0000950157-18-000694",
            "announced_at_precision": "sec_filing_date_utc_midnight",
            "trading_suspension": "prior_to_market_open_2018-06-15",
        },
    )


def frozen_lifecycle_event_specs(
    security_ids: dict[str, uuid.UUID],
    *,
    evidence_artifact_id: uuid.UUID,
    created_by: str,
) -> tuple[SecurityLifecycleEventSpec, ...]:
    evidence = (LifecycleEvidenceRef(evidence_artifact_id, "corporate_action_terms"),)
    return (
        SecurityLifecycleEventSpec(
            security_id=security_ids["scg"],
            event_key="scg_dominion_stock_merger_2019",
            version_number=1,
            event_type="stock_merger",
            event_status="confirmed",
            announced_at=datetime(2018, 1, 2, 12, tzinfo=UTC),
            effective_session=date(2019, 1, 1),
            last_trading_session=date(2018, 12, 31),
            settlement_session=date(2019, 1, 2),
            selectable_after=False,
            tradable_after=False,
            valuation_state_after="terminal",
            evidence=evidence,
            settlement_legs=(
                SecuritySettlementLegSpec(
                    leg_kind="successor_security",
                    target_security_id=security_ids["d"],
                    quantity_per_source_share=Decimal("0.6690"),
                    valuation_policy="successor_market_value",
                ),
            ),
            created_by=created_by,
            details={"evidence_object": "scg_dominion_completion_2019"},
        ),
        SecurityLifecycleEventSpec(
            security_id=security_ids["tss"],
            event_key="tss_global_payments_stock_merger_2019",
            version_number=1,
            event_type="stock_merger",
            event_status="confirmed",
            announced_at=datetime(2019, 5, 27, 12, tzinfo=UTC),
            effective_session=date(2019, 9, 18),
            last_trading_session=date(2019, 9, 17),
            settlement_session=date(2019, 9, 18),
            selectable_after=False,
            tradable_after=False,
            valuation_state_after="terminal",
            evidence=evidence,
            settlement_legs=(
                SecuritySettlementLegSpec(
                    leg_kind="successor_security",
                    target_security_id=security_ids["gpn"],
                    quantity_per_source_share=Decimal("0.8101"),
                    valuation_policy="successor_market_value",
                ),
            ),
            created_by=created_by,
            details={"evidence_object": "tss_global_payments_completion_2019"},
        ),
        SecurityLifecycleEventSpec(
            security_id=security_ids["abmd"],
            event_key="abmd_jnj_cash_merger_2022",
            version_number=1,
            event_type="cash_merger",
            event_status="confirmed",
            announced_at=datetime(2022, 11, 1, 12, tzinfo=UTC),
            effective_session=date(2022, 12, 22),
            last_trading_session=date(2022, 12, 21),
            settlement_session=date(2022, 12, 22),
            selectable_after=False,
            tradable_after=False,
            valuation_state_after="terminal",
            evidence=evidence,
            settlement_legs=(
                SecuritySettlementLegSpec(
                    leg_kind="cash",
                    cash_amount_per_source_share=Decimal("380.00"),
                    currency="USD",
                    valuation_policy="fixed_cash",
                ),
            ),
            created_by=created_by,
            details={
                "evidence_object": "abmd_jnj_completion_2022",
                "nontradeable_cvr_treatment": "excluded_from_research_valuation",
                "product_warning_required": True,
            },
        ),
    )


class FrozenSp500LifecyclePublicationService:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def publish(self, *, created_by: str) -> FrozenSp500LifecyclePublication:
        evidence = ExternalImportManifestService(self._engine).publish(
            official_lifecycle_evidence_spec(created_by=created_by)
        )
        with self._engine.connect() as connection:
            rows = connection.execute(
                text(
                    "SELECT security_key,security_id FROM catalog.security "
                    "WHERE security_key IN ('scg','d','tss','gpn','abmd')"
                )
            ).mappings()
            security_ids = {
                str(item["security_key"]): item["security_id"] for item in rows
            }
        if set(security_ids) != {"scg", "d", "tss", "gpn", "abmd"}:
            raise LookupError("Frozen lifecycle Security identities are incomplete")
        service = SecurityLifecycleEventService(self._engine)
        events = tuple(
            service.publish(spec)
            for spec in frozen_lifecycle_event_specs(
                security_ids,
                evidence_artifact_id=evidence.artifact_id,
                created_by=created_by,
            )
        )
        return FrozenSp500LifecyclePublication(evidence, events)


class FrozenSp500L3LifecyclePublicationService:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def publish(self, *, created_by: str) -> FrozenSp500LifecyclePublication:
        evidence = ExternalImportManifestService(self._engine).publish(
            official_l3_lifecycle_evidence_spec(created_by=created_by)
        )
        with self._engine.connect() as connection:
            rows = connection.execute(
                text(
                    "SELECT security_key,security_id FROM catalog.security "
                    "WHERE security_key IN ('lll','lhx')"
                )
            ).mappings()
            security_ids = {
                str(item["security_key"]): item["security_id"] for item in rows
            }
        if set(security_ids) != {"lll", "lhx"}:
            raise LookupError("L3Harris lifecycle Security identities are incomplete")
        event = SecurityLifecycleEventService(self._engine).publish(
            frozen_l3_lifecycle_event_spec(
                security_ids,
                evidence_artifact_id=evidence.artifact_id,
                created_by=created_by,
            )
        )
        return FrozenSp500LifecyclePublication(evidence, (event,))


class FrozenSp500AetLifecyclePublicationService:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def publish(self, *, created_by: str) -> FrozenSp500LifecyclePublication:
        evidence = ExternalImportManifestService(self._engine).publish(
            official_aet_lifecycle_evidence_spec(created_by=created_by)
        )
        with self._engine.connect() as connection:
            rows = connection.execute(
                text(
                    "SELECT security_key,security_id FROM catalog.security "
                    "WHERE security_key IN ('aet','cvs')"
                )
            ).mappings()
            security_ids = {
                str(item["security_key"]): item["security_id"] for item in rows
            }
        if set(security_ids) != {"aet", "cvs"}:
            raise LookupError("Aetna/CVS lifecycle Security identities are incomplete")
        event = SecurityLifecycleEventService(self._engine).publish(
            frozen_aet_lifecycle_event_spec(
                security_ids,
                evidence_artifact_id=evidence.artifact_id,
                created_by=created_by,
            )
        )
        return FrozenSp500LifecyclePublication(evidence, (event,))


class FrozenSp500EsrxLifecyclePublicationService:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def publish(self, *, created_by: str) -> FrozenSp500LifecyclePublication:
        evidence = ExternalImportManifestService(self._engine).publish(
            official_esrx_lifecycle_evidence_spec(created_by=created_by)
        )
        with self._engine.connect() as connection:
            rows = connection.execute(
                text(
                    "SELECT security_key,security_id FROM catalog.security "
                    "WHERE security_key IN ('esrx','ci')"
                )
            ).mappings()
            security_ids = {
                str(item["security_key"]): item["security_id"] for item in rows
            }
        if set(security_ids) != {"esrx", "ci"}:
            raise LookupError("Express Scripts/Cigna Security identities are incomplete")
        event = SecurityLifecycleEventService(self._engine).publish(
            frozen_esrx_lifecycle_event_spec(
                security_ids,
                evidence_artifact_id=evidence.artifact_id,
                created_by=created_by,
            )
        )
        return FrozenSp500LifecyclePublication(evidence, (event,))


class FrozenSp500TwxLifecyclePublicationService:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def publish(self, *, created_by: str) -> FrozenSp500LifecyclePublication:
        evidence = ExternalImportManifestService(self._engine).publish(
            official_twx_lifecycle_evidence_spec(created_by=created_by)
        )
        with self._engine.connect() as connection:
            rows = connection.execute(
                text(
                    "SELECT security_key,security_id FROM catalog.security "
                    "WHERE security_key IN ('twx','t')"
                )
            ).mappings()
            security_ids = {
                str(item["security_key"]): item["security_id"] for item in rows
            }
        if set(security_ids) != {"twx", "t"}:
            raise LookupError("Time Warner/AT&T Security identities are incomplete")
        event = SecurityLifecycleEventService(self._engine).publish(
            frozen_twx_lifecycle_event_spec(
                security_ids,
                evidence_artifact_id=evidence.artifact_id,
                created_by=created_by,
            )
        )
        return FrozenSp500LifecyclePublication(evidence, (event,))
