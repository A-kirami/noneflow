import abc
import html
import json
import re
from functools import cache
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import httpx
from nonebot import logger
from pydantic import BaseModel, ValidationError, root_validator, validator

from .config import plugin_config
from .constants import (
    ADAPTER_DESC_PATTERN,
    ADAPTER_HOMEPAGE_PATTERN,
    ADAPTER_MODULE_NAME_PATTERN,
    ADAPTER_NAME_PATTERN,
    BOT_DESC_PATTERN,
    BOT_HOMEPAGE_PATTERN,
    BOT_NAME_PATTERN,
    DETAIL_MESSAGE_TEMPLATE,
    PLUGIN_DESC_PATTERN,
    PLUGIN_HOMEPAGE_PATTERN,
    PLUGIN_MODULE_NAME_PATTERN,
    PLUGIN_NAME_PATTERN,
    PROJECT_LINK_PATTERN,
    PYPI_PACKAGE_NAME_PATTERN,
    PYTHON_MODULE_NAME_REGEX,
    TAGS_PATTERN,
    VALIDATION_MESSAGE_TEMPLATE,
)
from .models import PublishType

if TYPE_CHECKING:
    from githubkit.rest.models import Issue
    from githubkit.webhooks.models import Issue as WebhookIssue
    from githubkit.webhooks.models import (
        IssueCommentCreatedPropIssue,
        IssuesOpenedPropIssue,
        IssuesReopenedPropIssue,
    )
    from pydantic.error_wrappers import ErrorDict


class MyValidationError(ValueError):
    """验证错误错误"""

    def __init__(
        self,
        type: "PublishType",
        raw_data: dict[str, Any],
        errors: list["ErrorDict"],
    ) -> None:
        self.type = type
        self.raw_data = raw_data
        self.errors = errors

    @property
    def message(self) -> str:
        return generate_validation_message(self)


class Tag(BaseModel):
    """标签"""

    label: str
    color: str

    @validator("label", pre=True)
    def label_validator(cls, v: str) -> str:
        if len(v) > 10:
            raise ValueError("标签名称过长<dt>请确保标签名称不超过 10 个字符。</dt>")
        return v

    @validator("color", pre=True)
    def color_validator(cls, v: str) -> str:
        if not re.match(r"^#[0-9a-fA-F]{6}$", v):
            raise ValueError("标签颜色错误<dt>请确保标签颜色符合十六进制颜色码规则。</dt>")
        return v


class PublishInfo(abc.ABC, BaseModel):
    """发布信息"""

    name: str
    desc: str
    author: str
    homepage: str
    tags: list[Tag]
    is_official: bool = False

    @validator("homepage", pre=True)
    def homepage_validator(cls, v: str) -> str:
        if v:
            status_code = check_url(v)
            if status_code != 200:
                raise ValueError(
                    f"""⚠️ 项目 <a href="{v}">主页</a> 返回状态码 {status_code}。<dt>请确保您的项目主页可访问。</dt>"""
                )
        return v

    @validator("tags", pre=True)
    def tags_validator(cls, v: str) -> list[dict[str, str]]:
        try:
            tags: list[Any] | Any = json.loads(v)
        except json.JSONDecodeError:
            raise ValueError("⚠️ 标签解码失败。<dt>请确保标签为 JSON 格式。</dt>")
        if not isinstance(tags, list):
            raise ValueError("⚠️ 标签格式错误。<dt>请确保标签为列表。</dt>")
        if len(tags) > 0 and any(map(lambda x: not isinstance(x, dict), tags)):
            raise ValueError("⚠️ 标签格式错误。<dt>请确保标签列表内均为字典。</dt>")
        if len(tags) > 3:
            raise ValueError("⚠️ 标签数量过多。<dt>请确保标签数量不超过 3 个。</dt>")
        return tags

    def _update_file(self, path: Path):
        logger.info(f"正在更新文件: {path}")
        with path.open("r", encoding="utf-8") as f:
            data: list[dict[str, str]] = json.load(f)
        with path.open("w", encoding="utf-8") as f:
            data.append(self.dict(exclude={"plugin_test_result"}))
            json.dump(data, f, ensure_ascii=False, indent=2)
            # 结尾加上换行符，不然会被 pre-commit fix
            f.write("\n")
        logger.info(f"文件更新完成")

    @abc.abstractmethod
    def update_file(self) -> None:
        """更新文件"""
        raise NotImplementedError

    @classmethod
    @abc.abstractmethod
    def from_issue(
        cls,
        issue: "IssuesOpenedPropIssue | IssuesReopenedPropIssue | IssueCommentCreatedPropIssue | Issue | WebhookIssue",
    ) -> "PublishInfo":
        """从议题中获取所需信息"""
        raise NotImplementedError

    @classmethod
    @abc.abstractmethod
    def get_type(cls) -> PublishType:
        """获取发布类型"""
        raise NotImplementedError

    @property
    def validation_message(self) -> str:
        """验证信息"""
        return generate_validation_message(self)

    @root_validator
    def prevent_duplication(cls, values: dict[str, Any]) -> dict[str, Any]:
        _type = cls.get_type()
        if _type == PublishType.BOT:
            return values

        module_name = values.get("module_name")
        project_link = values.get("project_link")
        if _type == PublishType.PLUGIN:
            with plugin_config.input_config.plugin_path.open(
                "r", encoding="utf-8"
            ) as f:
                data: list[dict[str, str]] = json.load(f)
        else:
            with plugin_config.input_config.adapter_path.open(
                "r", encoding="utf-8"
            ) as f:
                data: list[dict[str, str]] = json.load(f)

        if any(
            map(
                lambda x: x["module_name"] == module_name
                and x["project_link"] == project_link,
                data,
            )
        ):
            raise ValueError(
                f"⚠️ PyPI 项目名 {project_link} 加包名 {module_name} 的值与商店重复。<dt>请确保没有重复发布。</dt>"
            )
        return values


class PyPIMixin(BaseModel):
    module_name: str
    project_link: str

    @validator("module_name", pre=True)
    def module_name_validator(cls, v: str) -> str:
        if not PYTHON_MODULE_NAME_REGEX.match(v):
            raise ValueError(f"⚠️ 包名 {v} 不符合规范。<dt>请确保包名正确。</dt>")
        return v

    @validator("project_link", pre=True)
    def project_link_validator(cls, v: str) -> str:
        if not PYPI_PACKAGE_NAME_PATTERN.match(v):
            raise ValueError(f"⚠️ PyPI 项目名 {v} 不符合规范。<dt>请确保项目名正确。</dt>")

        if v and not check_pypi(v):
            raise ValueError(
                f'⚠️ 包 <a href="https://pypi.org/project/{v}/">{v}</a> 未发布至 PyPI。<dt>请将您的包发布至 PyPI。</dt>'
            )
        return v


class BotPublishInfo(PublishInfo):
    """发布机器人所需信息"""

    @classmethod
    def get_type(cls) -> PublishType:
        return PublishType.BOT

    def update_file(self) -> None:
        self._update_file(plugin_config.input_config.bot_path)

    @classmethod
    def from_issue(
        cls,
        issue: "IssuesOpenedPropIssue | IssuesReopenedPropIssue | IssueCommentCreatedPropIssue | Issue | WebhookIssue",
    ) -> "BotPublishInfo":
        body = issue.body if issue.body else ""

        name = BOT_NAME_PATTERN.search(body)
        desc = BOT_DESC_PATTERN.search(body)
        author = issue.user.login if issue.user else None
        homepage = BOT_HOMEPAGE_PATTERN.search(body)
        tags = TAGS_PATTERN.search(body)

        raw_data = {
            "name": name.group(1).strip() if name else None,
            "desc": desc.group(1).strip() if desc else None,
            "author": author,
            "homepage": homepage.group(1).strip() if homepage else None,
            "tags": tags.group(1).strip() if tags else None,
        }

        try:
            return BotPublishInfo(**raw_data)
        except ValidationError as e:
            raise MyValidationError(cls.get_type(), raw_data, e.errors())


class PluginPublishInfo(PublishInfo, PyPIMixin):
    """发布插件所需信息"""

    plugin_test_result: bool
    """插件测试结果"""

    @validator("plugin_test_result", pre=True)
    def plugin_test_result_validator(cls, v: bool) -> bool:
        if plugin_config.skip_plugin_test:
            logger.info("已跳过插件测试")
            return True

        if not v:
            raise ValueError(
                f"⚠️ 插件加载测试未通过。<details><summary>测试输出</summary>{html.escape(plugin_config.plugin_test_output)}</details>"
            )
        return v

    @classmethod
    def get_type(cls) -> PublishType:
        return PublishType.PLUGIN

    def update_file(self) -> None:
        self._update_file(plugin_config.input_config.plugin_path)

    @classmethod
    def from_issue(
        cls,
        issue: "IssuesOpenedPropIssue | IssuesReopenedPropIssue | IssueCommentCreatedPropIssue | Issue | WebhookIssue",
    ) -> "PluginPublishInfo":
        body = issue.body if issue.body else ""

        module_name = PLUGIN_MODULE_NAME_PATTERN.search(body)
        project_link = PROJECT_LINK_PATTERN.search(body)
        name = PLUGIN_NAME_PATTERN.search(body)
        desc = PLUGIN_DESC_PATTERN.search(body)
        author = issue.user.login if issue.user else None
        homepage = PLUGIN_HOMEPAGE_PATTERN.search(body)
        tags = TAGS_PATTERN.search(body)

        raw_data = {
            "module_name": module_name.group(1).strip() if module_name else None,
            "project_link": project_link.group(1).strip() if project_link else None,
            "name": name.group(1).strip() if name else None,
            "desc": desc.group(1).strip() if desc else None,
            "author": author,
            "homepage": homepage.group(1).strip() if homepage else None,
            "tags": tags.group(1).strip() if tags else None,
            "plugin_test_result": plugin_config.plugin_test_result,
        }

        try:
            return PluginPublishInfo(**raw_data)
        except ValidationError as e:
            raise MyValidationError(cls.get_type(), raw_data, e.errors())


class AdapterPublishInfo(PublishInfo, PyPIMixin):
    """发布适配器所需信息"""

    @classmethod
    def get_type(cls) -> PublishType:
        return PublishType.ADAPTER

    def update_file(self) -> None:
        self._update_file(plugin_config.input_config.adapter_path)

    @classmethod
    def from_issue(
        cls,
        issue: "IssuesOpenedPropIssue | IssuesReopenedPropIssue | IssueCommentCreatedPropIssue | Issue | WebhookIssue",
    ) -> "AdapterPublishInfo":
        body = issue.body if issue.body else ""

        module_name = ADAPTER_MODULE_NAME_PATTERN.search(body)
        project_link = PROJECT_LINK_PATTERN.search(body)
        name = ADAPTER_NAME_PATTERN.search(body)
        desc = ADAPTER_DESC_PATTERN.search(body)
        author = issue.user.login if issue.user else None
        homepage = ADAPTER_HOMEPAGE_PATTERN.search(body)
        tags = TAGS_PATTERN.search(body)

        raw_data = {
            "module_name": module_name.group(1).strip() if module_name else None,
            "project_link": project_link.group(1).strip() if project_link else None,
            "name": name.group(1).strip() if name else None,
            "desc": desc.group(1).strip() if desc else None,
            "author": author,
            "homepage": homepage.group(1).strip() if homepage else None,
            "tags": tags.group(1).strip() if tags else None,
        }

        try:
            return AdapterPublishInfo(**raw_data)
        except ValidationError as e:
            raise MyValidationError(cls.get_type(), raw_data, e.errors())


def check_pypi(project_link: str) -> bool:
    """检查项目是否存在"""
    url = f"https://pypi.org/pypi/{project_link}/json"
    status_code = check_url(url)
    return status_code == 200


@cache
def check_url(url: str) -> int | None:
    """检查网址是否可以访问

    返回状态码，如果报错则返回 None
    """
    logger.info(f"检查网址 {url}")
    try:
        r = httpx.get(url)
        return r.status_code
    except:
        pass


loc_map = {
    "name": "名称",
    "desc": "功能",
    "project_link": "PyPI 项目名",
    "module_name": "import 包名",
    "tags": "标签",
    "homepage": "项目仓库/主页链接",
}


def loc_to_name(loc: str) -> str:
    """将 loc 转换为可读名称"""
    if loc in loc_map:
        return loc_map[loc]
    return loc


def generate_validation_message(info: PublishInfo | MyValidationError) -> str:
    """生成验证信息"""
    if isinstance(info, MyValidationError):
        # 如果有错误
        publish_info: str = f"{info.type.value}: {info.raw_data['name'] or ''}"
        result = "⚠️ 在发布检查过程中，我们发现以下问题："

        errors: list[str] = []
        for error in info.errors:
            loc = cast(str, error["loc"][0])
            if loc == "tags" and len(error["loc"]) == 3:
                if error["type"] == "value_error.missing":
                    errors.append(
                        f"<li>⚠️ 第 {error['loc'][1]+1} 个标签缺少 {error['loc'][2]} 字段。<dt>请确保标签字段完整。</dt></li>"  # type: ignore
                    )
                else:
                    errors.append(f"<li>⚠️ 第 {error['loc'][1]+1} 个{error['msg']}</li>")  # type: ignore
            elif error["type"].startswith("type_error"):
                errors.append(
                    f"<li>⚠️ {loc_to_name(loc)}: 无法匹配到数据。<dt>请确保填写该项目。</dt></li>"
                )
            else:
                errors.append(f"<li>{error['msg']}</li>")

        error_message = "".join(errors)
        error_message = f"<pre><code>{error_message}</code></pre>"
    else:
        # 一切正常时
        publish_info = f"{info.get_type().value}: {info.name}"
        result = "✅ 所有测试通过，一切准备就绪！"
        error_message = ""

    detail_message = ""
    details: list[str] = []

    # 验证失败的项
    error_keys = (
        [error["loc"][0] for error in info.errors]
        if isinstance(info, MyValidationError)
        else []
    )

    # 标签
    tags = []
    if isinstance(info, PublishInfo):
        tags = [f"{tag.label}-{tag.color}" for tag in info.tags]
    elif "tags" not in error_keys:
        # 原始 tags 为 json 字符串
        # 需要先转换才能读取
        tags = [
            f"{tag['label']}-{tag['color']}"
            for tag in json.loads(info.raw_data["tags"])
        ]

    if tags:
        details.append(f"<li>✅ 标签: {', '.join(tags)}。</li>")

    # 主页
    homepage = ""
    if isinstance(info, PublishInfo):
        homepage = info.homepage
    elif "homepage" not in error_keys:
        homepage = info.raw_data["homepage"]
    if homepage:
        details.append(
            f"""<li>✅ 项目 <a href="{homepage}">主页</a> 返回状态码 {check_url(homepage)}。</li>"""
        )

    # 发布情况
    project_link = ""
    if isinstance(info, AdapterPublishInfo) or isinstance(info, PluginPublishInfo):
        project_link = info.project_link
    elif (
        isinstance(info, MyValidationError)
        and info.type in [PublishType.PLUGIN, PublishType.ADAPTER]
        and "project_link" not in error_keys
    ):
        project_link = info.raw_data["project_link"]
    if project_link:
        details.append(
            f"""<li>✅ 包 <a href="https://pypi.org/project/{project_link}/">{project_link}</a> 已发布至 PyPI。</li>"""
        )

    # 插件加载测试情况
    plugin_test_result = False
    if isinstance(info, PluginPublishInfo) and info.plugin_test_result:
        plugin_test_result = True
    elif (
        isinstance(info, MyValidationError)
        and info.type == PublishType.PLUGIN
        and "plugin_test_result" not in error_keys
    ):
        plugin_test_result = True

    # https://github.com/he0119/action-test/actions/runs/4469672520
    action_url = f"https://github.com/{plugin_config.github_repository}/actions/runs/{plugin_config.github_run_id}"
    if plugin_config.skip_plugin_test:
        details.append(f"""<li>✅ 插件 <a href="{action_url}">加载测试</a> 已跳过。</li>""")
    elif plugin_test_result:
        details.append(f"""<li>✅ 插件 <a href="{action_url}">加载测试</a> 通过。</li>""")

    if len(details) != 0:
        detail_message = "".join(details)
        detail_message = DETAIL_MESSAGE_TEMPLATE.format(detail_message=detail_message)

    return VALIDATION_MESSAGE_TEMPLATE.format(
        publish_info=publish_info,
        result=result,
        error_message=error_message,
        detail_message=detail_message,
    ).strip()
