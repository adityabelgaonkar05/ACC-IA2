import { useState } from "react";
import { Play, Folder, ShieldCheck } from "lucide-react";

type Props = {
  application: Record<string, any>;
  busy: boolean;
  onBuild: (spec: Record<string, any>) => void;
};

export default function Applications({
  application,
  busy,
  onBuild,
}: Props) {
  const [form, setForm] = useState({
    name: "My API",
    path: "",
    command: "sh run.sh",
    runtime: "python",
    port: 8080,
    health_path: "/health",
    load_path: "/work",
    version: "api-v1",
    cpu_limit: 1,
    memory_mb: 512,
  });
  const build = application?.build;
  const working = busy || ["Building", "Deploying"].includes(build?.status);
  return (
    <>
      <section className="panel">
        <div className="panel-heading">
          <div>
            <h2>Deploy your API</h2>
            <p>
              Provide a trusted local codebase. CloudRunner builds the image,
              starts healthy replicas, and routes traffic.
            </p>
          </div>
          <Folder size={20} className="purple" />
        </div>
        <form
          className="application-form"
          onSubmit={(event) => {
            event.preventDefault();
            onBuild(form);
          }}
        >
          <label>
            Application name
            <input
              required
              maxLength={60}
              value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
            />
          </label>
          <label>
            Version (new tag for every build)
            <input
              required
              pattern="[a-z0-9][a-z0-9_.-]{0,63}"
              value={form.version}
              onChange={(e) => setForm({ ...form, version: e.target.value })}
            />
          </label>
          <label className="wide">
            Absolute codebase path
            <input
              required
              placeholder="/path/to/your/api"
              value={form.path}
              onChange={(e) => setForm({ ...form, path: e.target.value })}
            />
          </label>
          <label className="wide">
            Start command
            <input
              required
              placeholder="sh run.sh"
              value={form.command}
              onChange={(e) => setForm({ ...form, command: e.target.value })}
            />
            <small>
              The process must listen on 0.0.0.0. Commands run inside Docker;
              use a script for shell expansion.
            </small>
          </label>
          <label>
            Build runtime
            <select
              value={form.runtime}
              onChange={(e) => setForm({ ...form, runtime: e.target.value })}
            >
              <option value="python">Python · requirements.txt</option>
              <option value="node">Node.js · package.json</option>
              <option value="dockerfile">Existing Dockerfile</option>
            </select>
          </label>
          <label>
            Container port
            <input
              required
              type="number"
              min={1024}
              max={65535}
              value={form.port}
              onChange={(e) =>
                setForm({ ...form, port: Number(e.target.value) })
              }
            />
          </label>
          <label>
            Health endpoint
            <input
              required
              value={form.health_path}
              onChange={(e) =>
                setForm({ ...form, health_path: e.target.value })
              }
            />
          </label>
          <label>
            GET endpoint for controlled load
            <input
              required
              value={form.load_path}
              onChange={(e) => setForm({ ...form, load_path: e.target.value })}
            />
            <small>
              Choose a safe, repeatable endpoint without side effects.
            </small>
          </label>
          <label>
            CPU quota per replica
            <select
              value={form.cpu_limit}
              onChange={(e) =>
                setForm({ ...form, cpu_limit: Number(e.target.value) })
              }
            >
              <option value={0.25}>0.25 CPU</option>
              <option value={0.5}>0.5 CPU</option>
              <option value={1}>1 CPU</option>
              <option value={2}>2 CPUs</option>
            </select>
          </label>
          <label>
            Memory per replica
            <select
              value={form.memory_mb}
              onChange={(e) =>
                setForm({ ...form, memory_mb: Number(e.target.value) })
              }
            >
              <option value={256}>256 MiB</option>
              <option value={512}>512 MiB</option>
              <option value={1024}>1024 MiB</option>
            </select>
          </label>
          <div className="application-actions wide">
            <button className="button primary" disabled={working}>
              <Play size={14} />
              {working ? "Building / deploying…" : "Build & deploy API"}
            </button>
          </div>
        </form>
      </section>
      {build && (
        <section className="panel">
          <div className="panel-heading">
            <div>
              <h2>
                {build.version} · {build.status}
              </h2>
              <p className="build-message">{build.message}</p>
            </div>
            <span className="badge">{build.status}</span>
          </div>
        </section>
      )}
      <section className="panel">
        <div className="panel-heading">
          <div>
            <h2>Automatic scaling policy</h2>
            <p>
              CPU usage ÷ configured CPU quota. High ≥70% for 3 samples scales
              out; low &lt;25% for 12 samples scales in. 6s scale-out / 45s
              scale-in cooldown. 1–5 replicas.
            </p>
          </div>
          <ShieldCheck size={20} />
        </div>
        <p className="application-note">
          CloudRunner hosts one active API at a time. A new healthy deployment
          replaces the previous API; older images remain available for rollback.
          Generic APIs get no Docker socket or host credentials. Use stateless
          APIs and trusted source code; builds can execute project installation
          scripts inside Docker.
        </p>
      </section>
    </>
  );
}
