# Fix: Metaflow UI DAG/Logs Not Rendering with Local Datastore

## The Bug

When using `METAFLOW_DEFAULT_DATASTORE=local` with the metaflow-dev stack (minikube), the UI Backend cannot render DAGs or logs because:

1. The UI Backend pod reads artifacts via the Metaflow Python client
2. Artifacts are stored on the macOS host at `~/.metaflow/` or `$PROJECT/.metaflow/`
3. The pod runs inside minikube's VM, which cannot see macOS paths
4. `content_addressed_store.py:140` gets `file_path=None` → `TypeError`
5. Log cache tries `shutil.move()` which fails on read-only mounts → `OSError: [Errno 30]`

## The Fix (3 steps, must be done every time minikube restarts)

### Step 1: Add a hostPath volume to the UI deployment (one-time)

```bash
# Add volume
kubectl patch deployment metaflow-ui --type='json' -p='[
  {"op": "add", "path": "/spec/template/spec/volumes/-",
   "value": {"name": "local-datastore",
             "hostPath": {"path": "/Users/landigf/Desktop/Code/GSoC/.metaflow",
                          "type": "Directory"}}},
  {"op": "add", "path": "/spec/template/spec/containers/0/volumeMounts/-",
   "value": {"name": "local-datastore",
             "mountPath": "/Users/landigf/Desktop/Code/GSoC/.metaflow",
             "readOnly": false}}
]'
```

**readOnly must be false** — the log cache uses `shutil.move()` which needs write access.

### Step 2: Mount host directory into minikube VM (every restart)

```bash
MINIKUBE=/Users/landigf/Desktop/Code/GSoC/venv/share/metaflow/devtools/.devtools/minikube/minikube
$MINIKUBE mount /Users/landigf/Desktop/Code/GSoC/.metaflow:/Users/landigf/Desktop/Code/GSoC/.metaflow &
```

This process must stay alive. The mount maps the macOS directory into the minikube VM at the same path, so the pod's hostPath volume can see the files.

### Step 3: Restart the UI pod (after mount is established)

```bash
kubectl delete pod -l app.kubernetes.io/name=metaflow-ui
```

The deployment will create a new pod that picks up the mount.

## Quick one-liner for after Docker restart

```bash
MINIKUBE=/Users/landigf/Desktop/Code/GSoC/venv/share/metaflow/devtools/.devtools/minikube/minikube && \
$MINIKUBE start 2>&1 | tail -3 && \
$MINIKUBE mount /Users/landigf/Desktop/Code/GSoC/.metaflow:/Users/landigf/Desktop/Code/GSoC/.metaflow & \
sleep 3 && \
kubectl delete pod $(kubectl get pods -o name | grep metaflow-ui-[0-9a-f] | head -1 | sed 's|pod/||') && \
sleep 10 && \
pkill -f "kubectl port-forward" 2>/dev/null; sleep 1 && \
kubectl port-forward svc/metaflow-service 8080:8080 &>/dev/null & \
kubectl port-forward svc/metaflow-ui 8083:8083 &>/dev/null & \
kubectl port-forward svc/metaflow-ui-static 3000:3000 &>/dev/null &
```

## Root Cause (potential upstream fix)

The UI Backend's `generate_dag_action.py:94` reads `_graph_info` via `DataArtifact(...).data`, which goes through the Metaflow client's `filecache.py` → `content_addressed_store.py`. When the local datastore root doesn't exist inside the container, `full_uri()` returns `None`.

A proper fix would be for the UI Backend to:
1. Check if `ds_type == "local"` and the path doesn't exist → return a clear error instead of crashing
2. Or: fall back to reading `_graph_info` from the metadata table instead of the datastore
3. Or: the DAG structure could be stored as metadata (not just as an artifact) so it's always queryable via SQL

This is worth filing as an issue on `Netflix/metaflow-service` since it affects anyone using local datastore with the UI.
