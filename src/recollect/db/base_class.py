# -*- coding: utf-8 -*-
import typing as t

from sqlalchemy.ext.declarative import declared_attr
from sqlalchemy.orm import as_declarative

class_registry: t.Dict = {}


@as_declarative(class_registry=class_registry)
class Base:
    __name__: str

    # Generate __tablename__ automatically
    @declared_attr
    def __tablename__(cls) -> str:
        return cls.__name__.lower()
