import yaml
import os
from pathlib import Path
import structlog

logger = structlog.get_logger()

class FeatureFlags:
    """
    Phase 12: Feature Flag System
    Enables safe rollout of new features. Agent works with ANY combination.
    """
    _instance = None
    _flags = {}
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._load_flags()
        return cls._instance
    
    def _load_flags(self):
        config_path = Path(__file__).parent / "feature_flags.yaml"
        if config_path.exists():
            with open(config_path, "r") as f:
                config = yaml.safe_load(f)
                self._flags = config.get("phase_12_features", {})
                logger.info("feature_flags_loaded", flags=self._flags)
        else:
            logger.warning("feature_flags_not_found", path=str(config_path))
            self._flags = {}
    
    def is_enabled(self, feature_name: str) -> bool:
        """Check if a feature is enabled."""
        return self._flags.get(feature_name, False)
    
    def enable(self, feature_name: str):
        """Enable a feature at runtime (for testing)."""
        self._flags[feature_name] = True
        logger.info("feature_enabled", feature=feature_name)
    
    def disable(self, feature_name: str):
        """Disable a feature at runtime."""
        self._flags[feature_name] = False
        logger.info("feature_disabled", feature=feature_name)
    
    def get_all(self) -> dict:
        """Return all feature flags."""
        return self._flags.copy()

# Singleton accessor
def get_flags() -> FeatureFlags:
    return FeatureFlags()

if __name__ == "__main__":
    flags = get_flags()
    print("Feature Flags Loaded:")
    for name, enabled in flags.get_all().items():
        status = "[ON]" if enabled else "[OFF]"
        print(f"  {name}: {status}")
