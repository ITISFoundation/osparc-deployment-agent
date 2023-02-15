from simcore_service_deployment_agent.models import WebserverExtraEnvirons


def test_model_example():
    WebserverExtraEnvirons.parse_obj(
        WebserverExtraEnvirons.Config.schema_extra["example"]
    )
