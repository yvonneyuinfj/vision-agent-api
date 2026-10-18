"""场景目录的对外形态（决策 52）：`GET /api/v1/scenes` 的响应模型。

`params` 沿用前端 `WorkbenchDef.params` 的口径：frozen＝该工作台有参数表，
draft＝契约未冻结、只声明不编造表单（决策 3）。
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

SceneParamsState = Literal["frozen", "draft"]


class SceneSpec(BaseModel):
    scene: str
    label: str = Field(description="侧栏与页头全称，如「孔探检测」")
    short: str = Field(description="列表/筛选器用简称，如「孔探」")
    en: str
    params: SceneParamsState = Field(description="frozen＝有参数表 · draft＝契约未冻结")
    input: str = Field(description="输入物料要求，与提交页校验口径同源")
    auto_recognize: bool = Field(
        description="该场景是否进了自动识别的关键词表；False 时只能人工显式选场景提交"
    )
