from typing import List, Optional
from pydantic import BaseModel, Field


class InstructionFlagSchema(BaseModel):
    """Annotation flag on a single instruction (schema >= 4.3)."""

    kind: str = Field(..., description="Flag type: VIN_SUSPECT, CHECKSUM_SUSPECT, etc.")
    reason: str = Field(..., description="Human-readable explanation")
    confidence: float = Field(..., description="0.0–1.0 confidence score")
    action: str = Field("REVIEW", description="WARN | SKIP | REVIEW")


class SupportedFamilySchema(BaseModel):
    manufacturer: str
    family: str
    extractor: str


class SupportedFamiliesResponseSchema(BaseModel):
    total: int
    families: List[SupportedFamilySchema]


class InstructionSchema(BaseModel):
    offset: int
    offset_hex: str
    size: int
    ob: str = Field(..., description="Original bytes at this offset (hex, uppercase)")
    mb: str = Field(..., description="Modified bytes to write (hex, uppercase)")
    ctx: str = Field(
        ...,
        description="Context bytes before the change — used as anchor (hex, uppercase)",
    )
    context_after: str
    context_size: int = Field(
        ...,
        description="Actual context anchor size in bytes (may exceed the configured minimum when auto-expanded)",
    )
    # Phase 1 entropy-gated context fields (present for schema >= 4.2)
    ctx_entropy: Optional[float] = Field(
        None,
        description="Shannon entropy of the context anchor in bits/byte. Present for schema >= 4.2.",
    )
    ctx_unique: Optional[bool] = Field(
        None,
        description="True when the ctx+ob anchor pattern is unique in the original binary. Present for schema >= 4.2.",
    )
    ctx_expanded: Optional[bool] = Field(
        None,
        description="True when the context was auto-expanded beyond the configured minimum size. Present for schema >= 4.2.",
    )
    description: str
    flags: List[InstructionFlagSchema] = Field(
        default_factory=list,
        description="Annotation flags for suspicious changes (VIN, checksum, etc.)",
    )


class ECUIdentitySchema(BaseModel):
    """
    Lean ECU identity block.

    Used in two places:
      - POST /identify response
      - Embedded as the ``ecu`` block inside every recipe (consumed by the
        patcher pipeline for size and SW-version pre-flight checks).
    """

    manufacturer: Optional[str] = None
    match_key: Optional[str] = None
    ecu_family: Optional[str] = None
    ecu_variant: Optional[str] = None
    software_version: Optional[str] = Field(
        None, description="Software version string — also used for SW revision check"
    )
    hardware_number: Optional[str] = Field(
        None,
        description="Bosch hardware part number — present only when reliably found in the binary",
    )
    calibration_id: Optional[str] = Field(
        None,
        description=(
            "Calibration sub-version identifier. "
            "For most ECU families this supplements software_version (e.g. ME7 cal dataset). "
            "For LH-Jetronic Format A it is the sole identifier and drives match_key."
        ),
    )
    oem_part_number: Optional[str] = None
    platform: Optional[str] = None
    calibration_version: Optional[str] = None
    serial_number: Optional[str] = None
    dataset_number: Optional[str] = None
    file_size: int = 0
    sha256: str = Field(default="", description="SHA-256 of the full binary file")
    cook_warnings: List[str] = Field(default_factory=list)


class CreatorSchema(BaseModel):
    """Recipe author identity (schema >= 4.3)."""

    model_config = {"extra": "ignore"}

    name: str = ""
    handle: str = ""
    id: str = ""
    created_at: str = Field("", description="ISO 8601 UTC timestamp")
    signature: Optional[str] = Field(None, description="Digital signature (future)")
    trust_level: str = Field(
        "UNSIGNED",
        description="UNSIGNED | COMMUNITY | SIGNED | VERIFIED",
    )


class AnalysisMetadataSchema(BaseModel):
    """Recipe metadata block (schema >= 4.3)."""

    model_config = {"extra": "ignore"}

    name: str = ""
    description: str = ""
    tags: List[str] = Field(default_factory=list)
    instruction_count: int = 0
    original_file: str = ""
    modified_file: str = ""
    original_size: int = 0
    modified_size: int = 0
    tune_id: Optional[str] = None


class AnalysisStatisticsSchema(BaseModel):
    """Recipe statistics block (schema >= 4.3)."""

    total_changes: int = 0
    total_bytes_changed: int = 0
    percentage_changed: float = 0.0
    single_byte_changes: int = 0
    multi_byte_changes: int = 0
    largest_change_size: int = 0
    smallest_change_size: int = 0
    min_context_size: int = Field(
        0,
        description="Minimum context anchor size configured for this analysis",
    )
    max_context_size: int = Field(
        0,
        description="Maximum context anchor size allowed during auto-expansion",
    )


# class AnalyzerResponseSchema(BaseModel):
#     """
#     Full analysis response — format-4.3 recipe ready for serialisation
#     and direct consumption by the patcher pipeline.
#     """
#
#     model_config = {"extra": "ignore"}
#
#     type: str = Field("recipe")
#     schema_version: str = Field("4.3")
#     source: str = Field(..., description="full_cook | tune_export")
#     application: str = Field(..., description="openremap-core | openremap-studio")
#     creator: CreatorSchema = Field(default_factory=CreatorSchema)
#     fingerprint: str = ""
#     metadata: AnalysisMetadataSchema = Field(default_factory=AnalysisMetadataSchema)
#     ecu: ECUIdentitySchema = Field(default_factory=ECUIdentitySchema)
#     statistics: AnalysisStatisticsSchema = Field(default_factory=AnalysisStatisticsSchema)
#     instructions: List[InstructionSchema] = Field(default_factory=list)
