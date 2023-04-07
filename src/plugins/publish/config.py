from pathlib import Path

from nonebot import get_driver
from pydantic import BaseModel, Extra, validator


class PublishConfig(BaseModel):
    base: str
    plugin_path: Path
    bot_path: Path
    adapter_path: Path


class Config(BaseModel, extra=Extra.ignore):
    input_config: PublishConfig
    github_repository: str
    github_run_id: str
    skip_plugin_test: bool = False
    plugin_test_result: bool = False
    plugin_test_output: str = ""

    @validator("plugin_test_result", pre=True)
    def plugin_test_result_validator(cls, v):
        # 如果插件测试没有运行时，会得到一个空字符串
        # 这里将其转换为布尔值，不然会报错
        if v == "":
            return False
        return v


plugin_config = Config.parse_obj(get_driver().config)