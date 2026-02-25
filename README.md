
```bash
kubectl create ns webhook-system
openssl req -new -x509 -nodes -keyout tls.key -out tls.crt -days 365 -subj "/CN=init-injector.example.com"
kubectl create secret tls webhook-tls-secret --cert=tls.crt --key=tls.key -n webhook-system
```

CA_BUNDLE=$(kubectl get secret webhook-tls-secret -n webhook-system -o jsonpath='{.data.ca\.crt}')


```
docker build -t djkormo/init-injector:latest .
```

```
docker run --rm -p 8443:8443 \
  -v "$(pwd)/tls.crt:/etc/webhook/certs/tls.crt:ro" \
  -v "$(pwd)/tls.key:/etc/webhook/certs/tls.key:ro" \
  djkormo/init-injector:latest
```

### 1. create a private CA
```
openssl genrsa -out ca.key 2048
```
### (optional) give it a human‑readable subject
```
openssl req -x509 -new -nodes \
    -key ca.key \
    -days 3650 \
    -subj "/CN=webhook-ca" \
    -out ca.crt
```

### 2. generate a server key & CSR for the webhook service
####    the CN/SAN must match the service DNS name used in the MutatingWebhookConfiguration\

```bash
openssl genrsa -out tls.key 2048
openssl req -new -key tls.key \
    -subj "/CN=init-injector.webhook-system.svc" \
    -out tls.csr
```

#### 3. sign the CSR with the CA

### the `-extfile` must contain a section matching the name passed to `-extensions`;
### here we create an in‑memory file with a `[v3_ext]` section containing the SANs.
### alternatively OpenSSL 1.1.1+ supports `-addext` which is even simpler.

```bash
openssl x509 -req -in tls.csr \
    -CA ca.crt -CAkey ca.key -CAcreateserial \
    -out tls.crt -days 3650 \
    -extensions v3_ext \
    -extfile <(printf "[v3_ext]\nsubjectAltName=DNS:init-injector.webhook-system.svc,DNS:init-injector.webhook-system.svc.cluster.local")
```
### OR, with a newer OpenSSL you can skip the extfile completely:
###
### openssl x509 -req -in tls.csr \
###     -CA ca.crt -CAkey ca.key -CAcreateserial \
###     -out tls.crt -days 3650 \
###     -addext "subjectAltName=DNS:init-injector.webhook-system.svc,DNS:init-injector.webhook-system.svc.cluster.local"

```bash
openssl x509 -req -in tls.csr \
    -CA ca.crt -CAkey ca.key -CAcreateserial \
    -out tls.crt -days 3650 \
    -extfile <(printf "subjectAltName=DNS:init-injector.webhook-system.svc,DNS:init-injector.webhook-system.svc.cluster.local")


kubectl -n webhook-system create secret tls webhook-tls-secret \
        --cert=tls.crt --key=tls.key

kubectl -n webhook-system logs deploy/init-injector

```