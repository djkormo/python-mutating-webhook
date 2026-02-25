import logging
import os
import sys
import json


class Config():


    configmap_name = os.getenv('CONFIGMAP_NAMESPACE', 'kopf-cleanup-operator-config')

    namespace = os.getenv('NAMESPACE')
    operator_namespace = os.getenv('OPERATOR_NAMESPACE')
    interval = float(os.getenv('INTERVAL', 30))
    LOOP_INTERVAL = float(os.getenv('INTERVAL', 30))
    RETRY_INTERVAL = "60s"
    # Define the operator version
    OPERATOR_VERSION = os.getenv('OPERATOR_VERSION', 'unknown')
    # Read commit hash and build time from environment variables
    COMMIT_HASH = os.getenv('COMMIT_HASH', 'unknown')
    BUILD_TIME = os.getenv('BUILD_TIME', 'unknown')

    CLEAN_CONFIGURATION = []

    PAUSE_ANNOTATION = "cleanup-pods-operator/pauseReconciliation"

    CONFIG_MAP_DATA = {
        "excludedNamespaces": "kube-system,default,cert-manager,openshift-config,openshift-monitoring,openshift-operators,openshift-storage,openshift-ingress",
        "loopInterval": "60s",
        "pauseReconciliationAnnotation": PAUSE_ANNOTATION,
        "retryInterval": "60m",
        "enabledControllers": "Deployment,StateFullSet,Job,CronJob",
        "cleanupConfiguration": '[["Failed": "Evicted"]]',
    }


    # Logging
    @staticmethod
    def setup_logging():
        log_level = os.getenv('LOG_LEVEL', 'INFO')
        log_level = getattr(logging, log_level.upper())
        
        # Create a logger
        logger = logging.getLogger()
        if not logger.hasHandlers():

            logger.setLevel(log_level)

            # Create a stream handler that outputs to stdout
            handler = logging.StreamHandler(sys.stdout)
            handler.setLevel(log_level)

            # Create a formatter and add it to the handler
            formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
            handler.setFormatter(formatter)

            # Add the handler to the logger
            logger.addHandler(handler)

        return logger
    
    @staticmethod
    def cluster():
        """Name of the cluster"""
        return os.getenv('CLUSTER_NAME')

    @staticmethod
    def load_clean_configuration(data):
        if 'cleanupConfiguration' in data:
            try:
                Config.CLEAN_CONFIGURATION = json.loads(data['cleanupConfiguration'])
            except json.JSONDecodeError as json_error:
                print(f"Invalid cleanConfiguration value in ConfigMap {json_error} . Using default value.")
                Config.CLEAN_CONFIGURATION = [["Failed", "Evicted"]]
        else:
            Config.CLEAN_CONFIGURATION = []
