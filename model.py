# -*- coding: utf-8 -*-
import json
from datetime import datetime

from sqlalchemy import desc

from .setup import *


ModelSetting = P.ModelSetting


class ModelFreeGameItem(ModelBase):
    P = P
    __tablename__ = "ff_freegame_item"
    __bind_key__ = P.package_name

    id = db.Column(db.Integer, primary_key=True)
    created_time = db.Column(db.DateTime)
    updated_time = db.Column(db.DateTime)
    external_id = db.Column(db.String)
    platform = db.Column(db.String)
    title = db.Column(db.String)
    image_url = db.Column(db.String)
    store_url = db.Column(db.String)
    original_price = db.Column(db.Float)
    current_price = db.Column(db.Float)
    discount_pct = db.Column(db.Integer)
    is_free_period = db.Column(db.Boolean)
    free_end = db.Column(db.String)
    rating = db.Column(db.Float)
    rating_count = db.Column(db.Integer)
    genres_json = db.Column(db.Text)

    def __init__(self):
        now = datetime.now()
        self.created_time = now
        self.updated_time = now
        self.genres_json = "[]"

    @property
    def genres(self):
        try:
            return json.loads(self.genres_json or "[]")
        except Exception:
            return []

    def as_dict(self):
        return {
            "id": self.id,
            "external_id": self.external_id,
            "platform": self.platform,
            "title": self.title,
            "image_url": self.image_url,
            "store_url": self.store_url,
            "original_price": self.original_price,
            "current_price": self.current_price,
            "discount_pct": self.discount_pct,
            "is_free_period": self.is_free_period,
            "free_end": self.free_end,
            "rating": self.rating,
            "rating_count": self.rating_count,
            "genres": self.genres,
            "updated_time": self.updated_time.strftime("%Y-%m-%d %H:%M:%S") if self.updated_time else "",
        }

    @classmethod
    def upsert(cls, data):
        row = F.db.session.query(cls).filter_by(
            external_id=str(data.get("external_id") or ""),
            platform=str(data.get("platform") or ""),
        ).first()
        if row is None:
            row = cls()
            row.external_id = str(data.get("external_id") or "")
            row.platform = str(data.get("platform") or "")
            F.db.session.add(row)
        row.title = str(data.get("title") or "")
        row.image_url = str(data.get("image_url") or "")
        row.store_url = str(data.get("store_url") or "")
        row.original_price = float(data.get("original_price") or 0)
        row.current_price = float(data.get("current_price") or 0)
        row.discount_pct = int(data.get("discount_pct") or 0)
        row.is_free_period = bool(data.get("is_free_period"))
        row.free_end = str(data.get("free_end") or "")
        row.rating = float(data.get("rating") or 0)
        row.rating_count = int(data.get("rating_count") or 0)
        row.genres_json = json.dumps(data.get("genres") or [], ensure_ascii=False)
        row.updated_time = datetime.now()
        return row

    @classmethod
    def delete_not_in_sources(cls, sources):
        with F.app.app_context():
            query = F.db.session.query(cls)
            if sources:
                query = query.filter(~cls.platform.in_(sources))
            deleted = query.delete(synchronize_session=False)
            F.db.session.commit()
            return deleted

    @classmethod
    def replace_source_items(cls, source_name, items):
        with F.app.app_context():
            F.db.session.query(cls).filter_by(platform=source_name).delete(synchronize_session=False)
            F.db.session.commit()
            for item in items:
                cls.upsert(item)
            F.db.session.commit()

    @classmethod
    def web_list(cls, req):
        with F.app.app_context():
            page = int(req.form.get("page", 1))
            search = str(req.form.get("search_word", "")).strip()
            platform = str(req.form.get("platform", "all")).strip()
            only_free = str(req.form.get("only_free", "true")).lower() == "true"
            query = F.db.session.query(cls)
            if search != "":
                query = query.filter(cls.title.like(f"%{search}%"))
            if platform not in ["", "all"]:
                query = query.filter(cls.platform == platform)
            query = query.filter((cls.is_free_period == True) | (cls.current_price == 0))
            query = query.order_by(desc(cls.discount_pct), desc(cls.updated_time))
            count = query.count()
            page_size = 30
            rows = query.limit(page_size).offset((page - 1) * page_size).all()
            return {
                "list": [row.as_dict() for row in rows],
                "paging": cls.get_paging_info(count, page, page_size),
            }

    @classmethod
    def get_platform_counts(cls):
        with F.app.app_context():
            rows = (
                F.db.session.query(cls.platform, db.func.count(cls.id))
                .group_by(cls.platform)
                .order_by(cls.platform.asc())
                .all()
            )
            return [{"platform": row[0], "count": row[1]} for row in rows]


class ModelFetchLog(ModelBase):
    P = P
    __tablename__ = "ff_freegame_fetch_log"
    __bind_key__ = P.package_name

    id = db.Column(db.Integer, primary_key=True)
    created_time = db.Column(db.DateTime)
    source = db.Column(db.String)
    status = db.Column(db.String)
    message = db.Column(db.Text)
    count = db.Column(db.Integer)

    def __init__(self, source, status, message="", count=0):
        self.created_time = datetime.now()
        self.source = source
        self.status = status
        self.message = message
        self.count = count
