"""SQLAlchemy ORM models for the Vigilus platform."""

from __future__ import annotations

import enum
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from vigilus.db.base import Base


# ────────────────────────────────────────────────────────────
# Enum types
# ────────────────────────────────────────────────────────────

class ProviderType(str, enum.Enum):
    anthropic = "anthropic"
    openai = "openai"
    openai_compat = "openai_compat"
    openrouter = "openrouter"
    google = "google"
    custom = "custom"


class PermissionLevel(str, enum.Enum):
    read = "read"
    write = "write"
    exec = "exec"
    elevate = "elevate"


class TrustMode(str, enum.Enum):
    strict = "strict"
    lenient = "lenient"
    inherit = "inherit"


class ToolImplementationType(str, enum.Enum):
    native = "native"
    http = "http"
    mcp = "mcp"


class McpTransport(str, enum.Enum):
    stdio = "stdio"
    sse = "sse"


class McpServerStatus(str, enum.Enum):
    stopped = "stopped"
    running = "running"
    error = "error"


class ServerStatus(str, enum.Enum):
    online = "online"
    offline = "offline"
    unknown = "unknown"


class CredentialType(str, enum.Enum):
    ssh_key = "ssh_key"
    password = "password"
    api_key = "api_key"
    token = "token"


class SshAuthMethod(str, enum.Enum):
    key = "key"
    password = "password"


class MessageRole(str, enum.Enum):
    user = "user"
    assistant = "assistant"
    tool = "tool"


class ActionOutcome(str, enum.Enum):
    pending = "pending"
    success = "success"
    error = "error"
    denied = "denied"


class JitPermission(str, enum.Enum):
    # read appears when a read-level call is denied for another reason
    # (e.g. outside the operator's working_dir) and needs JIT approval.
    read = "read"
    write = "write"
    exec = "exec"
    elevate = "elevate"


class JitStatus(str, enum.Enum):
    pending = "pending"
    approved = "approved"
    denied = "denied"
    expired = "expired"
    revoked = "revoked"


class ScopeSource(str, enum.Enum):
    """Where a Scope observation (scan / finding) came from."""
    nmap = "nmap"
    wazuh = "wazuh"
    manual = "manual"
    custom = "custom"


class FindingKind(str, enum.Enum):
    alert = "alert"
    vulnerability = "vulnerability"
    fim = "fim"            # file integrity monitoring
    exposure = "exposure"  # e.g. telnet open, risky service


class FindingSeverity(str, enum.Enum):
    info = "info"
    low = "low"
    medium = "medium"
    high = "high"
    critical = "critical"


# ────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────

def _uuid() -> str:
    return str(uuid4())


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ────────────────────────────────────────────────────────────
# Models
# ────────────────────────────────────────────────────────────

class Provider(Base):
    __tablename__ = "providers"

    id = Column(String(36), primary_key=True, default=_uuid)
    name = Column(String(255), unique=True, nullable=False, index=True)
    type = Column(Enum(ProviderType), nullable=False)
    base_url = Column(String(1024), nullable=True)
    api_key = Column(Text, nullable=True)
    default_model = Column(String(255), nullable=True)
    extra_headers = Column(JSON, nullable=True, default=dict)
    tool_calling_supported = Column(Boolean, default=True, nullable=False)
    enabled = Column(Boolean, default=True, nullable=False)
    is_default = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False)

    # relationships
    operators = relationship("Operator", back_populates="provider")


class Operator(Base):
    __tablename__ = "operators"

    id = Column(String(36), primary_key=True, default=_uuid)
    name = Column(String(255), unique=True, nullable=False, index=True)
    description = Column(Text, nullable=False)
    system_prompt = Column(Text, nullable=True)
    soul = Column(Text, nullable=True)
    provider_id = Column(String(36), ForeignKey("providers.id"), nullable=True)
    model = Column(String(255), nullable=True)
    permission_level = Column(Enum(PermissionLevel), nullable=False, default=PermissionLevel.read)
    trust_mode = Column(Enum(TrustMode), nullable=False, default=TrustMode.inherit)
    working_dir = Column(String(1024), nullable=True)
    is_builtin = Column(Boolean, default=False, nullable=False)
    delegatable = Column(Boolean, default=True, nullable=False)
    enabled = Column(Boolean, default=True, nullable=False)
    icon = Column(String(255), nullable=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False)

    # relationships
    provider = relationship("Provider", back_populates="operators")
    operator_tools = relationship("OperatorTool", back_populates="operator", cascade="all, delete-orphan")
    messages = relationship("Message", back_populates="operator")
    actions = relationship("Action", back_populates="operator")
    jit_requests = relationship("JitRequest", back_populates="operator")
    sessions = relationship("Session", back_populates="operator")


class Tool(Base):
    __tablename__ = "tools"

    id = Column(String(36), primary_key=True, default=_uuid)
    name = Column(String(255), unique=True, nullable=False, index=True)
    description = Column(Text, nullable=True)
    input_schema = Column(JSON, nullable=True, default=dict)
    implementation_type = Column(Enum(ToolImplementationType), nullable=False, default=ToolImplementationType.native)
    required_permission = Column(Enum(PermissionLevel), nullable=False, default=PermissionLevel.read)
    native_handler = Column(String(255), nullable=True)
    http_config = Column(JSON, nullable=True)
    mcp_server_id = Column(String(36), ForeignKey("mcp_servers.id"), nullable=True)
    mcp_tool_name = Column(String(255), nullable=True)
    is_builtin = Column(Boolean, default=False, nullable=False)
    available = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)

    # relationships
    operator_tools = relationship("OperatorTool", back_populates="tool", cascade="all, delete-orphan")
    mcp_server = relationship("McpServer", back_populates="tools")
    actions = relationship("Action", back_populates="tool")


class OperatorTool(Base):
    __tablename__ = "operator_tools"

    operator_id = Column(String(36), ForeignKey("operators.id", ondelete="CASCADE"), primary_key=True)
    tool_id = Column(String(36), ForeignKey("tools.id", ondelete="CASCADE"), primary_key=True)
    assigned_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)

    # relationships
    operator = relationship("Operator", back_populates="operator_tools")
    tool = relationship("Tool", back_populates="operator_tools")


class McpServer(Base):
    __tablename__ = "mcp_servers"

    id = Column(String(36), primary_key=True, default=_uuid)
    name = Column(String(255), unique=True, nullable=False, index=True)
    command = Column(String(1024), nullable=False)
    args = Column(JSON, nullable=True, default=list)
    env_vars = Column(JSON, nullable=True, default=dict)
    transport = Column(Enum(McpTransport), nullable=False, default=McpTransport.stdio)
    sse_url = Column(String(1024), nullable=True)
    status = Column(Enum(McpServerStatus), nullable=False, default=McpServerStatus.stopped)
    autostart = Column(Boolean, default=False, nullable=False)
    github_url = Column(String(1024), nullable=True)
    install_command = Column(String(1024), nullable=True)
    working_dir = Column(String(1024), nullable=True)
    last_started_at = Column(DateTime(timezone=True), nullable=True)
    last_error = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)

    # relationships
    tools = relationship("Tool", back_populates="mcp_server")


class Server(Base):
    __tablename__ = "servers"

    id = Column(String(36), primary_key=True, default=_uuid)
    name = Column(String(255), unique=True, nullable=False, index=True)
    hostname = Column(String(1024), nullable=False)
    port = Column(Integer, default=22, nullable=False)
    os = Column(String(255), nullable=True)          # OS type/name, e.g. "Ubuntu", "Debian"
    os_version = Column(String(255), nullable=True)  # OS version, e.g. "22.04"; auto-filled from scans
    tags = Column(JSON, nullable=True, default=list)
    credential_id = Column(String(36), ForeignKey("credentials.id"), nullable=True)
    notes = Column(Text, nullable=True)
    last_seen = Column(DateTime(timezone=True), nullable=True)
    status = Column(Enum(ServerStatus), nullable=False, default=ServerStatus.unknown)
    # Scope additions (additive, nullable) — ip is the reliable join key for
    # matching discovered hosts to managed inventory; origin badges provenance.
    ip = Column(String(64), nullable=True, index=True)
    origin = Column(String(32), nullable=True)  # manual | discovered | wazuh | mixed
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False)

    # relationships
    credential = relationship("Credential", back_populates="servers")
    actions = relationship("Action", back_populates="server")


class Credential(Base):
    __tablename__ = "credentials"

    id = Column(String(36), primary_key=True, default=_uuid)
    name = Column(String(255), unique=True, nullable=False, index=True)
    type = Column(Enum(CredentialType), nullable=False)
    ssh_auth_method = Column(Enum(SshAuthMethod), nullable=True)
    username = Column(String(255), nullable=True)
    secret = Column(Text, nullable=False)
    passphrase = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)

    # relationships
    servers = relationship("Server", back_populates="credential")


class Session(Base):
    __tablename__ = "sessions"

    id = Column(String(36), primary_key=True, default=_uuid)
    title = Column(String(512), nullable=True)
    operator_context = Column(Text, nullable=True)
    operator_id = Column(String(36), ForeignKey("operators.id"), nullable=True)
    origin = Column(String(32), nullable=True)  # web | telegram | discord | schedule
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)
    last_active_at = Column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False)

    # relationships
    messages = relationship("Message", back_populates="session", cascade="all, delete-orphan", order_by="Message.created_at")
    actions = relationship("Action", back_populates="session")
    operator = relationship("Operator", back_populates="sessions")


class Message(Base):
    __tablename__ = "messages"

    id = Column(String(36), primary_key=True, default=_uuid)
    session_id = Column(String(36), ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False, index=True)
    role = Column(Enum(MessageRole), nullable=False)
    content = Column(JSON, nullable=False)
    operator_id = Column(String(36), ForeignKey("operators.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)

    # relationships
    session = relationship("Session", back_populates="messages")
    operator = relationship("Operator", back_populates="messages")


class Action(Base):
    __tablename__ = "actions"

    id = Column(String(36), primary_key=True, default=_uuid)
    event = Column(String(255), nullable=False, index=True)
    actor = Column(String(255), nullable=False)
    operator_id = Column(String(36), ForeignKey("operators.id"), nullable=True)
    tool_id = Column(String(36), ForeignKey("tools.id"), nullable=True)
    tool_name = Column(String(255), nullable=True)
    server_id = Column(String(36), ForeignKey("servers.id"), nullable=True)
    args = Column(JSON, nullable=True)
    outcome = Column(Enum(ActionOutcome), nullable=False, default=ActionOutcome.pending)
    error = Column(Text, nullable=True)
    duration_ms = Column(Float, nullable=True)
    session_id = Column(String(36), ForeignKey("sessions.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)

    # relationships
    operator = relationship("Operator", back_populates="actions")
    tool = relationship("Tool", back_populates="actions")
    server = relationship("Server", back_populates="actions")
    session = relationship("Session", back_populates="actions")


class ScheduledTask(Base):
    """A recurring task sent to the Vigilus orchestrator on a cron schedule."""

    __tablename__ = "scheduled_tasks"

    id = Column(String(36), primary_key=True, default=_uuid)
    name = Column(String(255), unique=True, nullable=False, index=True)
    description = Column(Text, nullable=True)
    cron_expression = Column(String(128), nullable=False)  # standard 5-field cron
    task_prompt = Column(Text, nullable=False)             # message sent to the orchestrator
    operator_id = Column(String(36), ForeignKey("operators.id"), nullable=True)  # optional: delegate hint
    enabled = Column(Boolean, default=True, nullable=False)
    last_run_at = Column(DateTime(timezone=True), nullable=True)
    next_run_at = Column(DateTime(timezone=True), nullable=True)
    last_status = Column(String(32), nullable=True)        # success | error | running
    last_result = Column(JSON, nullable=True)              # {summary, session_id, error}
    deliver_to = Column(JSON, nullable=True)               # {"platform","chat_id"} channel delivery
    run_count = Column(Integer, default=0, nullable=False)
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False)

    # relationships
    operator = relationship("Operator")


class Memory(Base):
    """A durable learned fact about the environment or an agent's own work.

    *scope* is "global" (shared environment knowledge visible to every agent),
    "orchestrator" (private to the Vigilus orchestrator), or an operator's ID
    (private to that operator).
    """

    __tablename__ = "memories"

    id = Column(String(36), primary_key=True, default=_uuid)
    scope = Column(String(64), nullable=False, index=True, default="global")
    content = Column(Text, nullable=False)
    category = Column(String(64), nullable=True)  # e.g. server, service, preference
    source = Column(String(255), nullable=True)   # who saved it (operator name / "vigilus" / "user")
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False)


class User(Base):
    __tablename__ = "users"

    id = Column(String(36), primary_key=True, default=_uuid)
    username = Column(String(64), unique=True, nullable=False, index=True)
    password_hash = Column(String(256), nullable=False)
    token_version = Column(Integer, nullable=False, default=0)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)
    last_login_at = Column(DateTime(timezone=True), nullable=True)


class JitRequest(Base):
    __tablename__ = "jit_requests"

    id = Column(String(36), primary_key=True, default=_uuid)
    operator_id = Column(String(36), ForeignKey("operators.id"), nullable=False, index=True)
    resource = Column(String(1024), nullable=False)
    permission = Column(Enum(JitPermission), nullable=False)
    task_description = Column(Text, nullable=False)
    status = Column(Enum(JitStatus), nullable=False, default=JitStatus.pending)
    token_id = Column(String(255), nullable=True)
    ttl_minutes = Column(Integer, nullable=False)
    # How a granted approval may be reused: "once" = single command only
    # (excluded from the token-reuse lookup, so the next call re-prompts);
    # "timed" = reusable for ttl_minutes. Set when the request is approved.
    scope_mode = Column(String(16), nullable=False, default="timed")
    requested_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)
    resolved_at = Column(DateTime(timezone=True), nullable=True)
    approved_by = Column(String(255), nullable=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)

    # relationships
    operator = relationship("Operator", back_populates="jit_requests")


class ChannelConfig(Base):
    """One connected bot per platform."""

    __tablename__ = "channel_configs"

    id = Column(String(36), primary_key=True, default=_uuid)
    platform = Column(String(32), nullable=False, index=True)   # telegram | discord
    bot_token_enc = Column(Text, nullable=False)                # Fernet-encrypted
    bot_username = Column(String(255), nullable=True)
    enabled = Column(Boolean, default=True, nullable=False)
    respond_in_groups = Column(Boolean, default=False, nullable=False)
    default_operator_id = Column(String(36), ForeignKey("operators.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False)
    __table_args__ = (UniqueConstraint("platform", name="uq_channel_config_platform"),)


class ChannelAccount(Base):
    """Allowlist + identity mapping for external senders. DEFAULT-DENY."""

    __tablename__ = "channel_accounts"

    id = Column(String(36), primary_key=True, default=_uuid)
    platform = Column(String(32), nullable=False, index=True)
    external_user_id = Column(String(64), nullable=False)
    user_id = Column(String(36), ForeignKey("users.id"), nullable=True)  # identity map
    allowed = Column(Boolean, default=False, nullable=False)
    label = Column(String(255), nullable=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)
    __table_args__ = (
        UniqueConstraint("platform", "external_user_id", name="uq_channel_account"),
    )


class ChannelChat(Base):
    """Maps (platform, chat) -> Vigilus Session for context continuity."""

    __tablename__ = "channel_chats"

    id = Column(String(36), primary_key=True, default=_uuid)
    platform = Column(String(32), nullable=False, index=True)
    external_chat_id = Column(String(64), nullable=False)
    session_id = Column(String(36), ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False)
    last_active_at = Column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False)
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)
    __table_args__ = (
        UniqueConstraint("platform", "external_chat_id", name="uq_channel_chat"),
    )


class SearchConfig(Base):
    """Active web-research config (single row). UI-editable; DB wins over env.

    Mirrors ``ChannelConfig``: the Firecrawl API key is Fernet-encrypted at rest
    (``core/crypto.py``) and never returned by the API.
    """

    __tablename__ = "search_configs"

    id = Column(String(36), primary_key=True, default=_uuid)
    search_backend = Column(String(32), nullable=False, default="searxng")   # searxng|firecrawl
    fetch_backend = Column(String(32), nullable=False, default="builtin")    # builtin|firecrawl
    searxng_url = Column(String(1024), nullable=True)
    firecrawl_api_key_enc = Column(Text, nullable=True)                      # Fernet-encrypted
    enabled = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False)


# ────────────────────────────────────────────────────────────
# Scope — network attack-surface data (scans, discovered hosts, findings)
# ────────────────────────────────────────────────────────────

class Scan(Base):
    """One scan run (nmap or otherwise). Output is parsed into
    DiscoveredHost/DiscoveredService rows."""

    __tablename__ = "scans"

    id = Column(String(36), primary_key=True, default=_uuid)
    source = Column(Enum(ScopeSource), nullable=False, default=ScopeSource.nmap)
    target = Column(String(512), nullable=True)            # CIDR / host / range scanned
    status = Column(String(32), nullable=False, default="completed")  # running|completed|error
    host_count = Column(Integer, default=0, nullable=False)
    started_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)
    finished_at = Column(DateTime(timezone=True), nullable=True)
    operator_id = Column(String(36), ForeignKey("operators.id"), nullable=True)
    raw = Column(Text, nullable=True)                       # raw nmap XML (for re-parse/debug)
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)

    hosts = relationship("DiscoveredHost", back_populates="scan", cascade="all, delete-orphan")


class DiscoveredHost(Base):
    """A host seen by a scan. matched_server_id links it to managed inventory."""

    __tablename__ = "discovered_hosts"

    id = Column(String(36), primary_key=True, default=_uuid)
    scan_id = Column(String(36), ForeignKey("scans.id", ondelete="CASCADE"), nullable=False)
    ip = Column(String(64), nullable=False, index=True)
    mac = Column(String(64), nullable=True)
    hostname = Column(String(512), nullable=True)
    os_guess = Column(String(255), nullable=True)
    status = Column(String(32), nullable=False, default="up")   # up|down
    matched_server_id = Column(String(36), ForeignKey("servers.id", ondelete="SET NULL"), nullable=True, index=True)
    first_seen = Column(DateTime(timezone=True), default=_utcnow, nullable=False)
    last_seen = Column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False)

    scan = relationship("Scan", back_populates="hosts")
    services = relationship("DiscoveredService", back_populates="host", cascade="all, delete-orphan")
    matched_server = relationship("Server", foreign_keys=[matched_server_id])
    __table_args__ = (UniqueConstraint("scan_id", "ip", name="uq_discovered_host_scan_ip"),)


class DiscoveredService(Base):
    """An open port/service observed on a discovered host."""

    __tablename__ = "discovered_services"

    id = Column(String(36), primary_key=True, default=_uuid)
    discovered_host_id = Column(String(36), ForeignKey("discovered_hosts.id", ondelete="CASCADE"), nullable=False)
    port = Column(Integer, nullable=False)
    proto = Column(String(16), nullable=False, default="tcp")
    state = Column(String(32), nullable=False, default="open")  # open|filtered|closed
    service = Column(String(64), nullable=True)                 # http, ssh, ...
    product = Column(String(255), nullable=True)                # Apache, OpenSSH, ...
    version = Column(String(255), nullable=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)

    host = relationship("DiscoveredHost", back_populates="services")
    __table_args__ = (UniqueConstraint("discovered_host_id", "port", "proto", name="uq_disc_service_port"),)


class Finding(Base):
    """A security observation attached to a host. Unified across sources.

    A finding resolves to a graph node via whichever key is set:
    server_id (managed), discovered_host_id (scan-only), or host_identifier
    (external / unresolved). Idempotent via fingerprint."""

    __tablename__ = "findings"

    id = Column(String(36), primary_key=True, default=_uuid)
    source = Column(Enum(ScopeSource), nullable=False, default=ScopeSource.wazuh)
    kind = Column(Enum(FindingKind), nullable=False)
    severity = Column(Enum(FindingSeverity), nullable=False, default=FindingSeverity.info)
    title = Column(String(512), nullable=False)
    detail = Column(JSON, nullable=True)                 # source-specific payload (CVE, rule, etc.)
    server_id = Column(String(36), ForeignKey("servers.id", ondelete="CASCADE"), nullable=True, index=True)
    discovered_host_id = Column(String(36), ForeignKey("discovered_hosts.id", ondelete="CASCADE"), nullable=True, index=True)
    host_identifier = Column(String(255), nullable=True)  # IP/hostname when neither FK applies
    fingerprint = Column(String(255), nullable=True, index=True)  # dedupe key (source+kind+title+host)
    count = Column(Integer, default=1, nullable=False)
    first_seen = Column(DateTime(timezone=True), default=_utcnow, nullable=False)
    last_seen = Column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False)
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)

    __table_args__ = (UniqueConstraint("fingerprint", name="uq_finding_fingerprint"),)
