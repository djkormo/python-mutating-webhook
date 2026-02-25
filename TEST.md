# forward the webhook service to localhost
kubectl -n webhook-system port-forward svc/init-injector 8443:443 &

# craft a minimal AdmissionReview for a Pod
cat <<'EOF' > ar.json
{
  "apiVersion": "admission.k8s.io/v1",
  "kind": "AdmissionReview",
  "request": {
    "uid": "test",
    "kind": { "group": "", "version": "v1", "kind": "Pod" },
    "resource": { "group": "", "version": "v1", "resource": "pods" },
    "operation": "CREATE",
    "object": {
      "metadata": {
        "name": "foo",
        "namespace": "default",
        "labels": { "init-injection": "enabled" }          # match your namespace‑selector
      },
      "spec": {
        "containers": [{ "name": "busy", "image": "busybox" }]
      }
    }
  }
}
EOF

curl -k -X POST https://localhost:8443/mutate \
     -H "Content-Type: application/json" \
     --data-binary @ar.json


docker run --rm -p 8443:8443 \
    -v "$(pwd)/tls.crt:/etc/webhook/certs/tls.crt:ro" \
    -v "$(pwd)/tls.key:/etc/webhook/certs/tls.key:ro" \
    djkormo/init-injector:latest

# in another shell
curl -k https://localhost:8443/mutate -H 'Content-Type: application/json' \
     -d @ar.json