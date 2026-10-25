import React, { useState, useEffect, useCallback } from "react";
import { createRoot } from "react-dom/client";
import {
  AreaChart,
  Area,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
} from "recharts";
import {
  Cloud,
  LayoutDashboard,
  GitBranch,
  Code2,
  Activity,
  ArrowUpRight,
  ArrowRight,
  RefreshCw,
  Play,
  Square,
  Check,
  Copy,
  ExternalLink,
  Server,
  Cpu,
  Layers,
  Terminal,
  ShieldCheck,
  ChevronDown,
  CircleHelp,
  Radio,
  AlertCircle,
  RotateCcw,
} from "lucide-react";
import "./style.css";
import Applications from "./Applications";
type Any = Record<string, any>;
let token = "";
async function api(path: string, body?: Any) {
  const r = await fetch("/api/" + path, {
    method: body ? "POST" : "GET",
    headers: {
      "Content-Type": "application/json",
      ...(body ? { "X-CloudRunner-Token": token } : {}),
    },
    ...(body ? { body: JSON.stringify(body) } : {}),
  });
  const data = await r.json();
  if (!r.ok)
    throw new Error(
      typeof data.detail === "string"
        ? data.detail
        : JSON.stringify(data.detail),
    );
  return data;
}
const nav = [
  { name: "Applications", icon: Layers },
  { name: "Overview", icon: LayoutDashboard },
  { name: "Deployments", icon: GitBranch },
  { name: "Code playground", icon: Code2 },
  { name: "Scaling & monitoring", icon: Activity },
];
const sample =
  'name = input() or "CloudRunner"\nprint(f"Hello, {name}!")\n\n# Your code runs in an isolated container.\nfor i in range(3):\n    print(f"Execution {i + 1}: ready to launch 🚀")';
function App() {
  const [page, setPage] = useState("Overview"),
    [data, setData] = useState<Any | null>(null),
    [releases, setReleases] = useState<Any[]>([]),
    [metrics, setMetrics] = useState<Any>({ cpu: [], instances: [] }),
    [activities, setActivities] = useState<Any[]>([]),
    [policies, setPolicies] = useState<Any[]>([]),
    [load, setLoad] = useState<Any>({ status: "idle" }),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false),
    [notice, setNotice] = useState(""),
    [code, setCode] = useState(sample),
    [stdin, setStdin] = useState("CloudRunner"),
    [output, setOutput] = useState<Any | null>(null),
    [running, setRunning] = useState(false),
    [duration, setDuration] = useState(300),
    [concurrency, setConcurrency] = useState(4),
    [copied, setCopied] = useState(false);
  const refresh = useCallback(async () => {
    try {
      const s = await api("session");
      token = s.token;
      const [d, r, m, a, p, l] = await Promise.all(
        ["status", "releases", "metrics", "activities", "policies", "load"].map(
          (x) => api(x),
        ),
      );
      setData(d);
      setReleases(r);
      setMetrics(m);
      setActivities(a);
      setPolicies(p);
      setLoad(l);
      setError(d.monitor_error || "");
    } catch (e) {
      setError((e as Error).message);
    }
  }, []);
  useEffect(() => {
    refresh();
    const t = setInterval(refresh, 5000);
    return () => clearInterval(t);
  }, [refresh]);
  const action = async (path: string, body: Any) => {
    setBusy(true);
    try {
      const r = await api(path, body);
      setNotice(
        r.refresh_id
          ? "Local controller accepted rollout " +
              r.refresh_id +
              " · waiting for rollout verification"
          : r.status,
      );
      await refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const run = async () => {
    setRunning(true);
    setOutput(null);
    try {
      setOutput(await api("execute", { code, stdin, language: "python" }));
    } catch (e) {
      setOutput({ stderr: (e as Error).message });
    } finally {
      setRunning(false);
    }
  };
  const generic = data?.application?.kind === "api";
  useEffect(() => {
    if (generic && page === "Code playground") setPage("Overview");
  }, [generic, page]);
  const group = data?.group,
    health = data?.health,
    mock = data?.mode === "mock",
    instances = group?.Instances || [],
    latest = data?.refreshes?.[0],
    active =
      (latest &&
        ["Pending", "InProgress", "Baking", "RollbackInProgress"].includes(
          latest.Status,
        )) ||
      ["Building", "Deploying"].includes(data?.application?.build?.status);
  const chart = (points: Any[], label: string, color = "#8b7cf8") => (
    <div className="chart">
      {points.length ? (
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart
            data={points.map((p) => ({
              time: new Date(p.Timestamp).toLocaleTimeString([], {
                hour: "2-digit",
                minute: "2-digit",
              }),
              value: p.Average,
            }))}
          >
            <defs>
              <linearGradient id={label} x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor={color} stopOpacity={0.24} />
                <stop offset="100%" stopColor={color} stopOpacity={0} />
              </linearGradient>
            </defs>
            <CartesianGrid stroke="#262833" vertical={false} />
            <XAxis
              dataKey="time"
              stroke="#737585"
              tickLine={false}
              axisLine={false}
              minTickGap={40}
            />
            <YAxis
              stroke="#737585"
              tickLine={false}
              axisLine={false}
              width={35}
            />
            <Tooltip
              contentStyle={{
                background: "#1b1c26",
                border: "1px solid #363747",
                borderRadius: 8,
              }}
            />
            <Area
              type="monotone"
              dataKey="value"
              name={label}
              stroke={color}
              fill={`url(#${label})`}
              strokeWidth={2}
            />
          </AreaChart>
        </ResponsiveContainer>
      ) : (
        <div className="empty-chart">
          <Activity size={28} />
          <span>No Docker samples yet</span>
          <small>
            {mock
              ? "Mock mode does not collect metrics."
              : "Real CPU samples appear about every 2–3 seconds."}
          </small>
        </div>
      )}
    </div>
  );
  const copy = () => {
    navigator.clipboard.writeText(data?.endpoint || "");
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };
  return (
    <div className="app">
      <aside className="sidebar">
        <a
          className="brand"
          href="#"
          onClick={(e) => {
            e.preventDefault();
            setPage("Overview");
          }}
        >
          <span className="brand-icon">
            <Cloud size={23} />
          </span>
          cloudrunner<span className="brand-dot">®</span>
        </a>
        <div className="workspace">
          <span className="workspace-avatar">C</span>
          <div>
            My workspace<small>Personal project</small>
          </div>
          <ChevronDown size={14} />
        </div>
        <div className="nav-label">WORKSPACE</div>
        <nav>
          {nav
            .filter((n) => !generic || n.name !== "Code playground")
            .map((n) => (
              <button
                key={n.name}
                onClick={() => setPage(n.name)}
                className={page === n.name ? "selected" : ""}
              >
                <n.icon size={18} />
                {n.name}
                {page === n.name && <span className="nav-dot" />}
              </button>
            ))}
        </nav>
        <div className="sidebar-bottom">
          <div className="isolation">
            <ShieldCheck size={18} />
            <span>
              Isolated by design<small>Disposable. Resource-limited.</small>
            </span>
          </div>
          <a
            href="https://docs.docker.com/engine/containers/run/"
            target="_blank"
            rel="noreferrer"
          >
            <CircleHelp size={16} />
            Docker documentation
            <ArrowUpRight size={14} />
          </a>
          <div className="profile">
            <span className="avatar">LC</span>
            <div>
              Local console<small>Docker · no cloud account</small>
            </div>
            <span className="online-dot" />
          </div>
        </div>
      </aside>
      <div className="main">
        <header>
          <div className="breadcrumb">
            Workspace <span>/</span> <Cloud size={14} /> CloudRunner{" "}
            <span>/</span>
            <strong>{page}</strong>
          </div>
          <span className="local-pill">
            <span className="online-dot" />
            localhost <span className="keycap">⌘</span>
          </span>
        </header>
        <main>
          <div className="page-title">
            <div>
              <div className="eyebrow">
                <span className="online-dot" /> YOUR CLOUD, UNDER CONTROL
              </div>
              <h1>{page}</h1>
              <p>
                {page === "Overview"
                  ? "A clear view of your deployments, infrastructure, and performance."
                  : page === "Applications"
                    ? "Deploy a local API codebase and let CloudRunner manage its replicas."
                    : page === "Deployments"
                      ? "Immutable releases. Controlled rollouts. A clear path back."
                      : page === "Code playground"
                        ? "Write a little code. Run it in a fresh, isolated environment."
                        : "Watch your infrastructure respond to real demand."}
              </p>
            </div>
            <button
              className="button secondary"
              disabled={busy}
              onClick={refresh}
            >
              <RefreshCw size={15} />
              Refresh
            </button>
          </div>
          {error && (
            <div className="banner error">
              <AlertCircle size={17} />
              <span>{error}</span>
              <button onClick={() => setError("")}>Dismiss</button>
            </div>
          )}
          {notice && (
            <div className="banner">
              <Radio size={17} />
              <span>{notice}</span>
              <button onClick={() => setNotice("")}>Dismiss</button>
            </div>
          )}
          <div className="mode-banner">
            <span className={"status-dot " + (mock ? "amber" : "")} />
            <strong>
              {!data
                ? "Connecting to local backend"
                : mock
                  ? "Local development mode"
                  : "Local Docker mode"}
            </strong>
            <span>
              {mock
                ? "UI only. Switch to local mode to manage real containers."
                : "Real containers, local releases, and observed Docker metrics. No AWS account required."}
            </span>
            <span className="mode-label">
              {mock ? "MOCK · UI ONLY" : data ? "LOCAL · REAL" : "CONNECTING"}
            </span>
          </div>
          {page === "Applications" && (
            <Applications
              application={data?.application || {}}
              busy={busy || mock || !!active}
              onBuild={(spec) => action("application", spec)}
            />
          )}
          {page === "Overview" && (
            <>
              <section className="hero-card">
                <div className="hero-top">
                  <div className="service-name">
                    <span className="service-icon">
                      <Terminal size={23} />
                    </span>
                    <div>
                      <h2>
                        {data?.application?.name || "Code runner example"}{" "}
                        <span className="badge">
                          {data?.application?.runtime || "Python"}
                        </span>
                      </h2>
                      <span className="muted">
                        {generic
                          ? "Your deployed API"
                          : "Included example application"}
                      </span>
                    </div>
                  </div>
                  <span className={"health " + (health?.healthy ? "good" : "")}>
                    <span className="status-dot" />
                    {health?.healthy ? "Healthy" : "Unavailable"}
                  </span>
                </div>
                <div className="endpoint">
                  <span>APPLICATION ENDPOINT</span>
                  <div>
                    <code>{data?.endpoint || "Waiting for backend…"}</code>
                    <button title="Copy endpoint" onClick={copy}>
                      {copied ? <Check size={15} /> : <Copy size={15} />}
                    </button>
                    <a
                      aria-label="Open API health"
                      href={data?.endpoint + "/__cloudrunner/health"}
                      target="_blank"
                      rel="noreferrer"
                    >
                      <ExternalLink size={15} />
                    </a>
                  </div>
                </div>
                <div className="hero-bottom">
                  <span>
                    <GitBranch size={14} />{" "}
                    {health?.version?.slice(0, 12) || "—"}{" "}
                    <span className="muted">current API version</span>
                  </span>
                  <button onClick={() => setPage("Deployments")}>
                    View deployments <ArrowRight size={14} />
                  </button>
                </div>
              </section>
              <div className="group-state">
                <span>
                  <Server size={13} />
                  {group?.AutoScalingGroupName || "Local runner pool"}
                </span>
                <span>
                  {!group
                    ? "Waiting for Docker"
                    : group.Status ||
                      (group.SuspendedProcesses?.length
                        ? "Processes suspended"
                        : active
                          ? "Deployment active"
                          : instances.filter(
                                (i: Any) => i.LifecycleState === "InService",
                              ).length === group.DesiredCapacity
                            ? "Capacity matched"
                            : "Capacity changing")}
                </span>
              </div>
              <section className="stat-grid">
                {[
                  {
                    title: "Active instances",
                    value: group
                      ? instances.filter(
                          (i: Any) => i.LifecycleState === "InService",
                        ).length
                      : "—",
                    icon: Server,
                    foot: group
                      ? `${group.MinSize} min · ${group.MaxSize} max`
                      : "Awaiting Docker pool",
                    color: "purple",
                  },
                  {
                    title: "Desired capacity",
                    value: group?.DesiredCapacity ?? "—",
                    icon: Layers,
                    foot: "Managed by the local CPU controller",
                    color: "blue",
                  },
                  {
                    title: "Average CPU",
                    value: metrics.cpu.length
                      ? metrics.cpu.at(-1).Average.toFixed(1) + "%"
                      : "—",
                    icon: Cpu,
                    foot: "CPU relative to assigned quota",
                    color: "green",
                  },
                  {
                    title: "Deployment status",
                    value: active
                      ? "Rolling out"
                      : data?.verified
                        ? "Verified"
                        : latest?.Status || "No rollout",
                    icon: GitBranch,
                    foot: active
                      ? `${latest.PercentageComplete || 0}% complete`
                      : "Rollout + readiness verification",
                    color: "amber",
                  },
                ].map((s) => (
                  <article className="stat" key={s.title}>
                    <div>
                      <span>{s.title}</span>
                      <s.icon size={17} className={s.color} />
                    </div>
                    <strong>{s.value}</strong>
                    <small>{s.foot}</small>
                  </article>
                ))}
              </section>
              <div className="two-col">
                <section className="panel">
                  <div className="panel-heading">
                    <div>
                      <h2>CPU per runner</h2>
                      <p>Actual CPU usage relative to replica quotas</p>
                    </div>
                    <span className="period">
                      Last hour <ChevronDown size={12} />
                    </span>
                  </div>
                  {chart(metrics.cpu, "CPU")}
                  <div className="chart-footer">
                    <span>
                      <i />
                      CPU utilization
                    </span>
                    <span>2–3 second samples · Docker CPU</span>
                  </div>
                </section>
                {generic ? (
                  <section className="panel quick">
                    <div className="panel-heading">
                      <div>
                        <h2>Your API is deployed</h2>
                        <p>Requests are balanced across healthy replicas.</p>
                      </div>
                    </div>
                    <div className="code-preview">
                      <code>GET {data?.application?.load_path || "/"}</code>
                    </div>
                    <p className="quick-note">
                      CPU target: 70% of assigned quota · maximum 5 replicas
                    </p>
                    <a
                      className="button primary"
                      href={data?.endpoint}
                      target="_blank"
                      rel="noreferrer"
                    >
                      Open API <ArrowUpRight size={15} />
                    </a>
                    <button
                      className="button secondary"
                      onClick={() => setPage("Applications")}
                    >
                      Deploy a new codebase
                    </button>
                  </section>
                ) : (
                  <section className="panel quick">
                    <div className="panel-heading">
                      <div>
                        <h2>Ready to run?</h2>
                        <p>From a few lines to a live result.</p>
                      </div>
                      <Code2 size={20} className="purple" />
                    </div>
                    <div className="code-preview">
                      <span>
                        <b>1</b>
                        <em># Your next idea starts here</em>
                      </span>
                      <span>
                        <b>2</b>
                        <i>print</i>(<q>Hello, CloudRunner!</q>)
                      </span>
                      <span>
                        <b>3</b>
                      </span>
                      <span className="preview-output">
                        <b>›</b>Hello, CloudRunner!
                      </span>
                    </div>
                    <p className="quick-note">
                      <ShieldCheck size={14} />
                      No network. Limited resources. Fresh container.
                    </p>
                    <button
                      className="button primary"
                      onClick={() => setPage("Code playground")}
                    >
                      Open playground <ArrowUpRight size={15} />
                    </button>
                  </section>
                )}
              </div>
              <ActivityTable
                activities={activities}
                onMore={() => setPage("Scaling & monitoring")}
              />
            </>
          )}
          {page === "Deployments" && (
            <>
              <section className="panel">
                <div className="panel-heading">
                  <div>
                    <h2>Release pipeline</h2>
                    <p>
                      Local image → ready replacement pool → gateway switch →
                      health verification
                    </p>
                  </div>
                  <GitBranch size={20} className="purple" />
                </div>
                <div className="pipeline">
                  {[
                    "Versioned local image",
                    "Healthy replacement pool",
                    "Verified serving runners",
                  ].map((x, i) => (
                    <React.Fragment key={x}>
                      <div>
                        <span className="step">{i + 1}</span>
                        {x}
                      </div>
                      {i < 2 && <ArrowRight size={18} />}
                    </React.Fragment>
                  ))}
                </div>
                {latest && (
                  <div className="rollout">
                    <strong>
                      {latest.Status} · {latest.PercentageComplete ?? 0}%
                    </strong>
                    <progress
                      max="100"
                      value={latest.PercentageComplete || 0}
                    />
                    <p>
                      {latest.StatusReason ||
                        "The local controller is reporting rollout progress."}
                    </p>
                    <small>
                      {data?.verified
                        ? "Serving release verified healthy."
                        : "Waiting for replacement readiness and a healthy serving version."}
                    </small>
                  </div>
                )}
              </section>
              <section className="panel table-panel">
                <div className="panel-heading">
                  <div>
                    <h2>
                      Available releases{" "}
                      <span className="count">{releases.length}</span>
                    </h2>
                    <p>Versioned Docker images built on your computer</p>
                  </div>
                </div>
                <div className="table-scroll">
                  <table>
                    <thead>
                      <tr>
                        <th>RELEASE</th>
                        <th>STATUS</th>
                        <th>PUBLISHED</th>
                        <th>ACTIONS</th>
                      </tr>
                    </thead>
                    <tbody>
                      {releases.map((r) => (
                        <tr key={r.tag}>
                          <td>
                            <GitBranch size={14} />{" "}
                            <code title={r.tag}>{r.tag.slice(0, 12)}</code>
                            <small>{r.digest.slice(0, 22)}…</small>
                          </td>
                          <td>
                            <span className="badge">
                              {data?.verified && r.tag === data.current_tag
                                ? "Active · verified"
                                : r.tag === data?.current_tag
                                  ? "Pool selected"
                                  : "Available"}
                            </span>
                          </td>
                          <td>{new Date(r.pushed_at).toLocaleString()}</td>
                          <td>
                            <div className="row-actions">
                              <button
                                className="button secondary"
                                disabled={busy || active || mock}
                                onClick={() => action("deploy", { tag: r.tag })}
                              >
                                Deploy <ArrowUpRight size={13} />
                              </button>
                              <button
                                title="Rollback requires a previously healthy image"
                                className="button secondary"
                                disabled={busy || active || mock}
                                onClick={() =>
                                  action("rollback", { tag: r.tag })
                                }
                              >
                                <RotateCcw size={13} />
                                Rollback
                              </button>
                            </div>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                {!releases.length && (
                  <Empty
                    title="No releases available"
                    text={
                      mock
                        ? "Switch the backend to local mode to read Docker releases."
                        : "Run ./scripts/build-release.sh v1, then refresh."
                    }
                  />
                )}
              </section>
              <section className="panel">
                <div className="panel-heading">
                  <div>
                    <h2>Rollout history</h2>
                    <p>
                      Recorded local deployment outcomes, including automatic
                      rollback
                    </p>
                  </div>
                </div>
                {data?.refreshes?.length ? (
                  data.refreshes.map((r: Any) => (
                    <div className="history-row" key={r.InstanceRefreshId}>
                      <span>
                        <code>{r.InstanceRefreshId}</code>
                        <small>{r.StatusReason}</small>
                      </span>
                      <span className="badge">
                        {r.Status} · {r.PercentageComplete ?? 0}%
                      </span>
                    </div>
                  ))
                ) : (
                  <Empty
                    title="No rollout history"
                    text="Deployments appear after the local controller accepts a rollout."
                  />
                )}
              </section>
            </>
          )}
          {page === "Code playground" && (
            <>
              <div className="playground">
                <section className="panel editor-panel">
                  <div className="editor-toolbar">
                    <span>
                      <span className="file-dot" />
                      main.py
                    </span>
                    <label>
                      Language{" "}
                      <select aria-label="Language">
                        <option>Python 3.12</option>
                      </select>
                    </label>
                  </div>
                  <div className="editor">
                    <div className="line-numbers">
                      {code.split("\n").map((_, i) => (
                        <span key={i}>{i + 1}</span>
                      ))}
                    </div>
                    <textarea
                      aria-label="Python source code"
                      spellCheck={false}
                      value={code}
                      onChange={(e) => setCode(e.target.value)}
                      maxLength={16384}
                    />
                  </div>
                  <div className="editor-footer">
                    <span>
                      <ShieldCheck size={13} />
                      128 MiB · 0.5 CPU · 5s timeout
                    </span>
                    <span>UTF-8 · Python</span>
                  </div>
                </section>
                <section className="panel input-panel">
                  <div className="panel-heading">
                    <h2>Standard input</h2>
                    <span className="badge">optional</span>
                  </div>
                  <textarea
                    aria-label="Standard input"
                    placeholder="Input for your program…"
                    value={stdin}
                    maxLength={16384}
                    onChange={(e) => setStdin(e.target.value)}
                  />
                  <p>Passed to your program through stdin.</p>
                  <button
                    className="button primary"
                    disabled={running || !code.trim()}
                    onClick={run}
                  >
                    <Play size={15} />
                    {running ? "Executing…" : "Run code"}
                    <span>↵</span>
                  </button>
                </section>
              </div>
              <section className="panel terminal">
                <div className="panel-heading">
                  <h2>
                    <Terminal size={17} />
                    Output
                  </h2>
                  {output && (
                    <span className="badge">
                      {output.duration_ms ?? "—"} ms · exit{" "}
                      {output.exit_code ?? "—"}{" "}
                      {output.timed_out ? "· TIMED OUT" : ""}
                    </span>
                  )}
                </div>
                <pre>
                  {running
                    ? "Starting isolated execution container…"
                    : output?.stdout ||
                      (!output
                        ? "Run your code to see the result."
                        : "(no stdout)")}
                </pre>
                {output?.stderr && (
                  <pre className="stderr">{output.stderr}</pre>
                )}
              </section>
              <div className="security-note">
                <ShieldCheck size={19} />
                <div>
                  <strong>A fresh environment for every execution</strong>
                  <p>
                    Non-root user · No network access · Read-only filesystem ·
                    Dropped capabilities · CPU, memory and PID limits. Docker
                    isolation is for trusted college demonstrations; it is
                    insufficient for arbitrary hostile public workloads.
                  </p>
                </div>
              </div>
            </>
          )}
          {page === "Scaling & monitoring" && (
            <>
              <div className="two-col">
                <section className="panel">
                  <div className="panel-heading">
                    <div>
                      <h2>CPU per runner</h2>
                      <p>Observed Docker CPU, including execution jobs</p>
                    </div>
                    <Cpu size={18} />
                  </div>
                  {chart(metrics.cpu, "CPU")}
                </section>
                <section className="panel">
                  <div className="panel-heading">
                    <div>
                      <h2>Instances in service</h2>
                      <p>Observed local runner count</p>
                    </div>
                    <Server size={18} />
                  </div>
                  {chart(metrics.instances, "Instances", "#54c2ac")}
                </section>
              </div>
              <section className="panel">
                <div className="panel-heading">
                  <div>
                    <h2>Scaling configuration</h2>
                    <p>
                      Bounded local controller keeps resource usage consistent
                    </p>
                  </div>
                  <span className="badge">Read only</span>
                </div>
                <div className="config-grid">
                  <div>
                    <small>MINIMUM / DESIRED / MAXIMUM</small>
                    <strong>
                      {group
                        ? `${group.MinSize} / ${group.DesiredCapacity} / ${group.MaxSize}`
                        : "— / — / —"}
                    </strong>
                  </div>
                  <div>
                    <small>CPU TARGET</small>
                    <strong>
                      {policies.find((p) => p.TargetTrackingConfiguration)
                        ?.TargetTrackingConfiguration.TargetValue ?? "—"}
                      %
                    </strong>
                  </div>
                  <div>
                    <small>LOCAL RUNNER POOL</small>
                    <code>
                      {group?.AutoScalingGroupName || "Not connected"}
                    </code>
                  </div>
                </div>
              </section>
              <section className="panel table-panel">
                <div className="panel-heading">
                  <div>
                    <h2>Runner containers</h2>
                    <p>
                      Container identity, readiness and pinned image version
                    </p>
                  </div>
                  <span className="count">{instances.length}</span>
                </div>
                <div className="table-scroll">
                  <table>
                    <thead>
                      <tr>
                        <th>CONTAINER</th>
                        <th>HEALTH</th>
                        <th>LIFECYCLE</th>
                        <th>HOST</th>
                        <th>RELEASE</th>
                        <th>CPU / QUOTA</th>
                        <th>ACTIVE / REQUESTS</th>
                        <th>P95 LATENCY</th>
                      </tr>
                    </thead>
                    <tbody>
                      {instances.map((i: Any) => (
                        <tr key={i.InstanceId}>
                          <td>
                            <code>{i.InstanceId}</code>
                          </td>
                          <td>{i.HealthStatus}</td>
                          <td>{i.LifecycleState}</td>
                          <td>{i.AvailabilityZone}</td>
                          <td>
                            {i.LaunchTemplate?.Version}
                            <small>
                              {data?.instance_releases
                                ?.find(
                                  (r: Any) => r.instance_id === i.InstanceId,
                                )
                                ?.tag?.slice(0, 12)}
                            </small>
                          </td>
                          <td>
                            {i.CPUUtilization == null
                              ? "—"
                              : i.CPUUtilization.toFixed(1) + "%"}
                            <small>{i.CPUQuota ?? 1} CPU limit</small>
                          </td>
                          <td>
                            {data?.traffic?.[i.InstanceId]?.active || 0} /{" "}
                            {data?.traffic?.[i.InstanceId]?.requests || 0}
                          </td>
                          <td>
                            {data?.traffic?.[i.InstanceId]?.durations_ms?.length
                              ? Math.round(
                                  [
                                    ...data.traffic[i.InstanceId].durations_ms,
                                  ].sort((a: number, b: number) => a - b)[
                                    Math.ceil(
                                      data.traffic[i.InstanceId].durations_ms
                                        .length * 0.95,
                                    ) - 1
                                  ],
                                ) + " ms"
                              : "—"}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                {!instances.length && (
                  <Empty
                    title="No instance data"
                    text="Start ./scripts/local.sh to inspect your Docker pool."
                  />
                )}
              </section>
              <section className="panel load-panel">
                <div className="panel-heading">
                  <div>
                    <h2>Controlled load test</h2>
                    <p>
                      Requests to the active API’s configured load endpoint ·
                      maximum 10 minutes
                    </p>
                  </div>
                  <Activity size={20} className="purple" />
                </div>
                <div className="load-controls">
                  <label>
                    Duration
                    <select
                      value={duration}
                      onChange={(e) => setDuration(+e.target.value)}
                    >
                      <option value={60}>1 minute</option>
                      <option value={180}>3 minutes</option>
                      <option value={300}>5 minutes</option>
                      <option value={600}>10 minutes</option>
                    </select>
                  </label>
                  <label>
                    Concurrent clients
                    <select
                      value={concurrency}
                      onChange={(e) => setConcurrency(+e.target.value)}
                    >
                      {[2, 4, 8].map((v) => (
                        <option key={v}>{v}</option>
                      ))}
                    </select>
                  </label>
                  <button
                    className="button primary"
                    disabled={mock || busy || load.status === "running"}
                    onClick={() => action("load", { duration, concurrency })}
                  >
                    <Play size={14} />
                    Run load test
                  </button>
                  <button
                    className="button secondary"
                    disabled={load.status !== "running"}
                    onClick={() => action("load/cancel", {})}
                  >
                    <Square size={13} />
                    Cancel
                  </button>
                </div>
                <div className="load-state">
                  <span className="badge">{load.status}</span>
                  <span>
                    {load.completed || 0} completed · {load.errors || 0} failed
                    · {load.elapsed || 0}s elapsed
                  </span>
                </div>
                {load.duration > 0 && (
                  <progress value={load.elapsed || 0} max={load.duration} />
                )}
                <p className="load-note">
                  <AlertCircle size={14} />
                  Real Docker samples arrive every 2–3 seconds. Scale-out is
                  triggered by sustained CPU. Local runners share your computer;
                  scaling does not add physical CPU.
                </p>
              </section>
              <ActivityTable activities={activities} />
            </>
          )}
          <footer>
            <span>
              <Cloud size={14} />
              CloudRunner <span className="footer-divider">/</span>Small
              footprint. Real infrastructure.
            </span>
            <span>
              <span className="status-dot" />{" "}
              {mock
                ? "Local development"
                : data
                  ? "Docker connected"
                  : "Backend disconnected"}
              <span className="footer-divider">·</span> Auto-refreshes every 5s
            </span>
          </footer>
        </main>
      </div>
    </div>
  );
}
function Empty({ title, text }: { title: string; text: string }) {
  return (
    <div className="empty">
      <Layers size={23} />
      <strong>{title}</strong>
      <p>{text}</p>
    </div>
  );
}
function ActivityTable({
  activities,
  onMore,
}: {
  activities: Any[];
  onMore?: () => void;
}) {
  return (
    <section className="panel table-panel">
      <div className="panel-heading">
        <div>
          <h2>Scaling activity</h2>
          <p>The latest changes to your infrastructure</p>
        </div>
        {onMore && (
          <button className="text-button" onClick={onMore}>
            View all activity <ArrowRight size={14} />
          </button>
        )}
      </div>
      {activities.length ? (
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>ACTIVITY</th>
                <th>STATUS</th>
                <th>STARTED</th>
              </tr>
            </thead>
            <tbody>
              {activities.slice(0, onMore ? 4 : 30).map((a) => (
                <tr key={a.ActivityId}>
                  <td>
                    {a.Description}
                    <small>{a.Cause}</small>
                  </td>
                  <td>
                    <span className="badge">{a.StatusCode}</span>
                  </td>
                  <td>{new Date(a.StartTime).toLocaleString()}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <Empty
          title="All quiet here"
          text="Actual local container starts, deployments, and scaling events appear here."
        />
      )}
    </section>
  );
}

createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
