#!/usr/bin/env python3
"""
Kubernetes Mutating Webhook - Multi-Sidecar/Init Injector
ConfigMap name configurable via Deployment annotation: webhook-config.configmap-name
Supports: initContainers, containers, volumes, labels, annotations, imagePullSecrets
"""

import os
import base64
import json
import logging
from typing import List, Dict, Any, Optional
import yaml
import asyncio

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
import uvicorn
import kubernetes.client
import kubernetes.config
from kubernetes import client, config, watch
from kubernetes.client.rest import ApiException
from pprint import pprint
import kr8s
from kr8s.objects import Pod
from config import Config

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Kubernetes Multi-Sidecar Injector Webhook")

# Config settings
CONFIGMAP_KEY = "sidecars.yaml"
CONFIGMAP_ANNOTATION_KEY = "webhook-config.configmap-name"
CONFIGMAP_DEFAULT_NAME = "my-app-sidecar"
CONFIGMAP_NAMESPACE = os.getenv("NAMESPACE", "webhook-system")

# The webhook expects to run as the 'init-injector-sa' service account in the 'webhook-system' namespace.
# This service account must have permissions to read deployments and configmaps (see rbac.yaml).
# If running outside the cluster, ensure kubeconfig is available and has sufficient permissions.

cluster = Config.cluster()

def k8s_connect() -> client:

  try:
    # Try to load the in-cluster configuration
    config.load_incluster_config()
    logger.info("Loaded in-cluster configuration.")
  except config.ConfigException:
    # If that fails, fall back to kubeconfig file
    config.load_kube_config(context=cluster)
    logger.info(f"Loaded kubeconfig file with context {cluster}.")

  return client

def kubeconfig(insecure_skip_tls_verify=False) -> client.AppsV1Api:
    """This is for using the kubeconfig to auth with the k8s api
    with the first try it will try to use the in-cluster config (so for in cluster use)
    If it cannot find an incluster because it is running locally, it will use your local config"""
    try:
        # Try to load the in-cluster configuration
        config.load_incluster_config()
        logger.info("Loaded in-cluster configuration.")
    except config.ConfigException:
        # If that fails, fall back to kubeconfig file
        config.load_kube_config(context=cluster)
        logger.info(f"Loaded kubeconfig file with context {cluster}.")

        # Check the active context
        _, active_context = config.list_kube_config_contexts()
        if active_context:
            logger.info(f"The active context is {active_context['name']}.")
        else:
            logger.info("No active context.")

    # Now you can use the client
    api = client.AppsV1Api()

   # If insecure_skip_tls_verify is True, configure the client to skip TLS verification
    if insecure_skip_tls_verify:
        configuration = client.Configuration.get_default_copy()
        configuration.verify_ssl = False
        api = client.AppsV1Api(client.ApiClient(configuration))
        logger.info("Configured client to skip TLS verification.")

    return api



def get_api_client() -> kubernetes.client.ApiClient:
    """Get shared Kubernetes API client."""
    try:
        kubernetes.config.load_incluster_config()
        logger.info("Using in-cluster config (service account token and cluster env detected)")
        logger.debug(f"KUBERNETES_SERVICE_HOST={os.getenv('KUBERNETES_SERVICE_HOST')}, KUBERNETES_SERVICE_PORT={os.getenv('KUBERNETES_SERVICE_PORT')}")
        logger.debug("Service account files: " + ", ".join(os.listdir('/var/run/secrets/kubernetes.io/serviceaccount')))
    except Exception as e:
        logger.warning(f"In-cluster config failed: {e}")
        try:
            kubernetes.config.load_kube_config()
            logger.info("Using kubeconfig (local or mounted config)")
        except Exception as e2:
            logger.error(f"Failed to load kubeconfig: {e2}")
            raise
    config = kubernetes.client.Configuration()
    print("Config:->",config)
    return kubernetes.client.ApiClient(config)


def get_webhook_configmap_name() -> str:
    """Get ConfigMap name from webhook Deployment annotation."""
    try:
        #apps_api = get_apps_api()
        #config.load_kube_config()
        #api = kubernetes.client.CoreV1Api()
        api =k8s_connect().AppsV1Api()  #kubeconfig()
        namespace = CONFIGMAP_NAMESPACE
        #apps_api=client.CoreV1Api()
        # Find our Deployment by label app=init-injector
        deployments = api.list_namespaced_deployment(
            namespace=namespace, 
            label_selector="app=init-injector"
        )
        
        if not deployments.items:
            logger.warning("No deployment found with label app=init-injector, using default")
            return CONFIGMAP_DEFAULT_NAME
        
        deployment = deployments.items[0]
        annotations = deployment.metadata.annotations or {}
        
        configmap_name = annotations.get(CONFIGMAP_ANNOTATION_KEY, CONFIGMAP_DEFAULT_NAME)
        logger.info(f"Using ConfigMap '{configmap_name}' from deployment annotation")
        return configmap_name
        
    except ApiException as e:
        logger.warning(f"Failed to read deployment annotation, using default '{CONFIGMAP_DEFAULT_NAME}': {e}")
        return CONFIGMAP_DEFAULT_NAME

def load_sidecar_configs() -> List[Dict[str, Any]]:
    """Load sidecar configurations from dynamic ConfigMap."""
    configmap_name = get_webhook_configmap_name()
    #api = get_core_api()
    api =k8s_connect().CoreV1Api()  #kubeconfig()
    #api = kubernetes.client.CoreV1Api()
    #api = kubeconfig()
    
    try:
        cm = api.read_namespaced_config_map(
            name=configmap_name, 
            namespace=CONFIGMAP_NAMESPACE
        )
        yaml_str = cm.data.get(CONFIGMAP_KEY)
        if not yaml_str:
            raise ValueError(f"Key '{CONFIGMAP_KEY}' not found in ConfigMap '{configmap_name}'")
        
        configs = yaml.safe_load(yaml_str) or []
        if not isinstance(configs, list):
            raise ValueError(f"sidecars.yaml in '{configmap_name}' must be a list")
        
        logger.info(f"Loaded {len(configs)} sidecar configs from ConfigMap '{configmap_name}'")
        return configs
        
    except ApiException as e:
        if e.status == 404:
            raise HTTPException(status_code=500, detail=f"ConfigMap '{configmap_name}' not found in namespace '{CONFIGMAP_NAMESPACE}'")
        logger.error(f"K8s API error loading ConfigMap '{configmap_name}': {e}")
        raise HTTPException(status_code=500, detail=f"K8s error: {e}")
    except Exception as e:
        logger.error(f"Error parsing ConfigMap '{configmap_name}': {e}")
        raise HTTPException(status_code=500, detail=f"ConfigMap parse error: {e}")

def labels_match(pod_labels: Dict[str, str], match_labels: Dict[str, str]) -> bool:
    """Check if pod labels match required labels."""
    if not match_labels:
        return True
    return all(pod_labels.get(k) == v for k, v in match_labels.items())

def annotations_match(pod_annotations: Dict[str, str], match_annotations: Dict[str, str]) -> bool:
    """Check if pod annotations match required annotations."""
    if not match_annotations:
        return True
    return all(pod_annotations.get(k) == v for k, v in match_annotations.items())

def find_matching_configs(pod: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Find all sidecar configs that match this pod."""
    configs = load_sidecar_configs()
    pod_metadata = pod.get("metadata", {})
    pod_labels = pod_metadata.get("labels", {})
    pod_annotations = pod_metadata.get("annotations", {})
    
    matching = []
    for config in configs:
        match_labels = config.get("matchLabels", {})
        match_annotations = config.get("matchAnnotations", {})
        
        if (labels_match(pod_labels, match_labels) and 
            annotations_match(pod_annotations, match_annotations)):
            matching.append(config)
    
    logger.info(f"Found {len(matching)} matching configs for pod (labels={pod_labels}, annotations={list(pod_annotations.keys())})")
    return matching

def build_json_patch(pod: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Build comprehensive JSONPatch for matching sidecar configs."""
    patches = []
    matching_configs = find_matching_configs(pod)
    
    for config in matching_configs:
        config_name = config.get('name', 'unnamed')
        logger.info(f"Applying config '{config_name}'")
        
        # 1. initContainers
        init_containers = config.get("initContainers", [])
        if init_containers:
            spec = pod.setdefault("spec", {})
            existing = spec.get("initContainers", [])
            if not existing:
                patches.append({
                    "op": "add",
                    "path": "/spec/initContainers",
                    "value": init_containers
                })
            else:
                for container in init_containers:
                    patches.append({
                        "op": "add",
                        "path": "/spec/initContainers/-",
                        "value": container
                    })
        
        # 2. containers
        containers = config.get("containers", [])
        if containers:
            for container in containers:
                patches.append({
                    "op": "add",
                    "path": "/spec/containers/-",
                    "value": container
                })
        
        # 3. volumes
        volumes = config.get("volumes", [])
        if volumes:
            for volume in volumes:
                patches.append({
                    "op": "add",
                    "path": "/spec/volumes/-",
                    "value": volume
                })
        
        # 4. imagePullSecrets
        image_pull_secrets = config.get("imagePullSecrets", [])
        if image_pull_secrets:
            for secret in image_pull_secrets:
                patches.append({
                    "op": "add",
                    "path": "/spec/imagePullSecrets/-",
                    "value": secret
                })
        
        # 5. pod labels
        pod_labels = config.get("podLabels", {})
        for k, v in pod_labels.items():
            patches.append({
                "op": "add",
                "path": f"/metadata/labels/{k}",
                "value": v
            })
        
        # 6. pod annotations
        pod_annotations = config.get("podAnnotations", {})
        for k, v in pod_annotations.items():
            patches.append({
                "op": "add",
                "path": f"/metadata/annotations/{k}",
                "value": v
            })
    
    logger.info(f"Generated {len(patches)} patch operations")
    return patches

# --- AdmissionReview Models ---

class AdmissionReviewRequest:
    def __init__(self, data: Dict[str, Any]):
        self.uid = data.get("uid", "")
        self.kind = data.get("kind", {})
        self.namespace = data.get("namespace", "")
        self.operation = data.get("operation", "")
        self.object = data.get("object", {})
        self.dryRun = data.get("dryRun", False)

class AdmissionReviewResponse:
    def __init__(self, uid: str, allowed: bool = True, patch: Optional[str] = None):
        self.uid = uid
        self.allowed = allowed
        self.patchType = "JSONPatch" if patch else None
        self.patch = patch

# --- Endpoints ---

@app.get("/healthz")
async def healthz():
    """Liveness/readiness probe."""
    return {"status": "ok", "service": "multi-sidecar-injector"}

@app.post("/mutate", response_class=JSONResponse)
async def mutate(request: Request):
    """Mutate Pod admission webhook."""
    try:
        body = await request.json()
        req = AdmissionReviewRequest(body.get("request", {}))
        logger.info(f"Processing {req.operation} Pod in ns '{req.namespace}' (uid: {req.uid[:8]})")
        
        # Skip non-Pod CREATE
        if req.kind.get("kind") != "Pod" or req.operation != "CREATE":
            logger.debug("Skipping non-Pod CREATE")
            resp = AdmissionReviewResponse(req.uid, allowed=True)
            return admission_response(body["apiVersion"], resp)
        
        # Skip dry-run
        if req.dryRun:
            logger.debug("Skipping dry-run")
            resp = AdmissionReviewResponse(req.uid, allowed=True)
            return admission_response(body["apiVersion"], resp)
        
        # Build patch
        patch = build_json_patch(req.object)
        if not patch:
            logger.info("No matching configs - allowing unchanged")
            resp = AdmissionReviewResponse(req.uid, allowed=True)
        else:
            patch_b64 = base64.b64encode(json.dumps(patch).encode("utf-8")).decode("utf-8")
            logger.info(f"Generated {len(patch)} patch ops ({len(patch_b64)} bytes)")
            resp = AdmissionReviewResponse(req.uid, allowed=True, patch=patch_b64)
        
        return admission_response(body["apiVersion"], resp)
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Webhook error: {e}", exc_info=True)
        resp = AdmissionReviewResponse(req.uid if 'req' in locals() else "", allowed=False)
        return admission_response(body["apiVersion"], resp)

def admission_response(api_version: str, response: AdmissionReviewResponse) -> Dict[str, Any]:
    """Format AdmissionReview response."""
    result = {
        "apiVersion": api_version,
        "kind": "AdmissionReview",
        "response": {
            "uid": response.uid,
            "allowed": response.allowed,
        }
    }
    if response.patch:
        result["response"]["patch"] = response.patch
        result["response"]["patchType"] = response.patchType
    pprint(result)    
    return result




if __name__ == "__main__":
    """Start HTTPS server with TLS certs."""
    # Log the service account name if running in-cluster
    # Read the service account name from the SERVICE_ACCOUNT environment variable (set in Deployment)
    sa_name = os.getenv("SERVICE_ACCOUNT", "(unknown)")
    logger.info(f"Running as service account: {sa_name}")
    ssl_keyfile = "/etc/webhook/certs/tls.key"
    ssl_certfile = "/etc/webhook/certs/tls.crt"    
    print("Using certificate file: ",ssl_certfile)
    print("Using key file: ",ssl_keyfile)
    logger.info("Starting webhook server on 0.0.0.0:8443 (TLS)")

    if os.path.exists(ssl_keyfile) and os.path.exists(ssl_certfile):
        uvicorn.run(
            "main:app",
            host="0.0.0.0",
            port=8443,
            ssl_keyfile=ssl_keyfile,
            ssl_certfile=ssl_certfile,
            log_level="info"
        )
    else:
        logger.warning("TLS files not found, starting without SSL")
        uvicorn.run("main:app", host="0.0.0.0", port=8443, log_level="info")


