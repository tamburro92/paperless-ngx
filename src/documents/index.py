import logging  # noqa: EXE002

#import math
import os
from collections import Counter
from contextlib import contextmanager
from datetime import datetime
from datetime import timezone
from shutil import rmtree
from typing import Optional

import tantivy
from django.conf import settings
from django.db.models import QuerySet
from django.utils import timezone as django_timezone
from guardian.shortcuts import get_users_with_perms


from documents.models import CustomFieldInstance
from documents.models import Document
from documents.models import Note
from documents.models import User

logger = logging.getLogger("paperless.index")


def get_schema():
    tokenizer = "en_stem"
    schema_builder = tantivy.SchemaBuilder()

    schema_builder.add_unsigned_field("doc_id", stored=True)
    schema_builder.add_text_field("title", stored=True, tokenizer_name=tokenizer)
    schema_builder.add_text_field("content", stored=False, tokenizer_name=tokenizer)
    schema_builder.add_unsigned_field("asn", stored=True)
    schema_builder.add_text_field("correspondent", stored=True)
    schema_builder.add_unsigned_field("correspondent_id", stored=True)
    schema_builder.add_boolean_field("has_correspondent", stored=True)
    schema_builder.add_text_field("tag", stored=True)
    schema_builder.add_unsigned_field("tag_id", stored=True)
    schema_builder.add_boolean_field("has_tag", stored=True)
    schema_builder.add_text_field("type", stored=True)
    schema_builder.add_integer_field("type_id", stored=True)
    schema_builder.add_boolean_field("has_type", stored=True)
    schema_builder.add_date_field("created", stored=True)
    schema_builder.add_date_field("modified", stored=True)
    schema_builder.add_date_field("added", stored=True)
    schema_builder.add_text_field("path", stored=True)
    schema_builder.add_unsigned_field("path_id", stored=True)
    schema_builder.add_boolean_field("has_path", stored=True)
    schema_builder.add_text_field("notes", stored=True, tokenizer_name=tokenizer)
    schema_builder.add_unsigned_field("num_notes", stored=True)
    schema_builder.add_text_field("custom_fields", stored=True)
    schema_builder.add_unsigned_field("custom_field_count", stored=True)
    schema_builder.add_boolean_field("has_custom_fields", stored=True)
    schema_builder.add_unsigned_field("custom_fields_id", stored=True)
    schema_builder.add_text_field("owner", stored=True)
    schema_builder.add_unsigned_field("owner_id", stored=True)
    schema_builder.add_boolean_field("has_owner", stored=True)
    schema_builder.add_unsigned_field("viewer_id", stored=True)
    schema_builder.add_text_field("checksum", stored=True)
    schema_builder.add_text_field("original_filename", stored=True)
    schema_builder.add_boolean_field("is_shared", stored=True)

    schema = schema_builder.build()

    return schema


def optimize():
    writer = open_index().writer()
    writer.garbage_collect_files()
    writer.wait_merging_threads()

def open_index(recreate=False): #-> FileIndex:
    try:
        return tantivy.Index(schema=get_schema(), path=str(settings.INDEX_DIR), reuse=not recreate)
    except Exception:
        logger.exception("Error while opening the index, recreating.")

    if os.path.isdir(str(settings.INDEX_DIR)):
        rmtree(str(settings.INDEX_DIR))
    os.mkdir(str(settings.INDEX_DIR))

    return tantivy.Index(schema=get_schema(), path=str(settings.INDEX_DIR), reuse=False)


@contextmanager
def open_index_writer(): # -> AsyncWriter:
    writer = open_index().writer()

    try:
        yield writer
    except Exception as e:
        logger.exception(str(e))
        writer.rollback()
    else:
        writer.commit()
    finally:
        writer.wait_merging_threads()


#@contextmanager
def open_index_searcher(): # -> Searcher:
    return open_index().searcher()

#    try:
#        yield searcher
#    finally:
#        searcher.close()


def update_document(writer, doc: Document):
    tags = ",".join([t.name for t in doc.tags.all()])
    tags_ids = ",".join([str(t.id) for t in doc.tags.all()])
    notes = ",".join([str(c.note) for c in Note.objects.filter(document=doc)])
    custom_fields = ",".join(
        [str(c) for c in CustomFieldInstance.objects.filter(document=doc)],
    )
    custom_fields_ids = ",".join(
        [str(f.field.id) for f in CustomFieldInstance.objects.filter(document=doc)],
    )
    asn = doc.archive_serial_number
    if asn is not None and (
        asn < Document.ARCHIVE_SERIAL_NUMBER_MIN
        or asn > Document.ARCHIVE_SERIAL_NUMBER_MAX
    ):
        logger.error(
            f"Not indexing Archive Serial Number {asn} of document {doc.pk}. "
            f"ASN is out of range "
            f"[{Document.ARCHIVE_SERIAL_NUMBER_MIN:,}, "
            f"{Document.ARCHIVE_SERIAL_NUMBER_MAX:,}.",
        )
        asn = 0
    users_with_perms = get_users_with_perms(
        doc,
        only_with_perms_in=["view_document"],
    )
    viewer_ids = ",".join([str(u.id) for u in users_with_perms])
    tdoc = dict(
        doc_id=doc.pk,
        title=doc.title,
        content=doc.content,
        correspondent=doc.correspondent.name if doc.correspondent else None,
        correspondent_id=doc.correspondent.id if doc.correspondent else None,
        has_correspondent=doc.correspondent is not None,
        tag=tags if tags else None,
        tag_id=tags_ids if tags_ids else None,
        has_tag=len(tags) > 0,
        type=doc.document_type.name if doc.document_type else None,
        type_id=doc.document_type.id if doc.document_type else None,
        has_type=doc.document_type is not None,
        created=doc.created.timestamp(),
        added=doc.added.timestamp(),
        asn=asn,
        modified=doc.modified.timestamp(),
        path=doc.storage_path.name if doc.storage_path else None,
        path_id=doc.storage_path.id if doc.storage_path else None,
        has_path=doc.storage_path is not None,
        notes=notes,
        num_notes=len(notes),
        custom_fields=custom_fields,
        custom_field_count=len(doc.custom_fields.all()),
        has_custom_fields=len(custom_fields) > 0,
        custom_fields_id=custom_fields_ids if custom_fields_ids else None,
        owner=doc.owner.username if doc.owner else None,
        owner_id=doc.owner.id if doc.owner else None,
        has_owner=doc.owner is not None,
        viewer_id=viewer_ids if viewer_ids else None,
        checksum=doc.checksum,
        original_filename=doc.original_filename,
        is_shared=len(viewer_ids) > 0,
    )
    tdoc_no_none = {k: v for k, v in tdoc.items() if v is not None}

    writer.add_document(tantivy.Document(**tdoc_no_none))

    writer.commit()


def remove_document(writer, doc: Document):
    remove_document_by_id(writer, doc.pk)
    writer.commit()


def remove_document_by_id(writer, doc_id):
    writer.delete_documents("doc_id", doc_id)

def remove_document_from_index(document: Document):
    with open_index_writer() as writer:
        remove_document(writer, document)


def add_or_update_document(document: Document):
    with open_index_writer() as writer:
        update_document(writer, document)



# class MappedDocIdSet:
#     """
#     A DocIdSet backed by a set of `Document` IDs.
#     Supports efficiently looking up if a whoosh docnum is in the provided `filter_queryset`.
#     """

#     def __init__(self, filter_queryset: QuerySet, searcher: tantivy.Searcher) -> None:
#         super().__init__()
#         document_ids = filter_queryset.order_by("id").values_list("id", flat=True)
#         self.document_ids = list(document_ids)
#         self.searcher = searcher

#     def __contains__(self, docnum):
#         document_id = self.searcher.doc(docnum)["id"]
#         return document_id in self.document_ids

#     def __bool__(self):
#         # searcher.search ignores a filter if it's "falsy".
#         # We use this hack so this DocIdSet, when used as a filter, is never ignored.
#         return True


class DelayedQuery:
    def _get_query(self):
        raise NotImplementedError  # pragma: no cover

    def _get_query_sortedby(self):
        if "ordering" not in self.query_params:
            return None, False

        field: str = self.query_params["ordering"]

        sort_fields_map = {
            "created": "created",
            "modified": "modified",
            "added": "added",
            "title": "title",
            "correspondent__name": "correspondent",
            "document_type__name": "type",
            "archive_serial_number": "asn",
            "num_notes": "num_notes",
            "owner": "owner",
        }

        if field.startswith("-"):
            field = field[1:]
            reverse = True
        else:
            reverse = False

        if field not in sort_fields_map:
            return None, False
        else:
            return sort_fields_map[field], reverse

    def __init__(
        self,
        searcher: tantivy.Searcher,
        query_params,
        page_size,
        filter_queryset: QuerySet,
    ):
        self.searcher = searcher
        self.query_params = query_params
        self.page_size = page_size
        self.saved_results = dict()
        self.first_score = None
        self.filter_queryset = filter_queryset

    def __len__(self):
        page = self[0:1]
        return len(page)

    def __getitem__(self, item):
        if item.start in self.saved_results:
            return self.saved_results[item.start]

        q, mask = self._get_query()
        sortedby, reverse = self._get_query_sortedby()

        page = self.searcher.search(
            q,
            offset = item.start,
            limit = self.page_size,
            order_by_field = sortedby,
            order = tantivy.Order.Desc if reverse else tantivy.Order.Asc,
        )
        #page.results.fragmenter = highlight.ContextFragmenter(surround=50)
        #page.results.formatter = HtmlFormatter(tagname="span", between=" ... ")

        #i#f not self.first_score and len(page.results) > 0 and sortedby is None:
        #    self.first_score = page.results[0].score

        #page.results.top_n = list(
        #    map(
        #        lambda hit: (
        #            (hit[0] / self.first_score) if self.first_score else None,
        #            hit[1],
        #        ),
        #        page.results.top_n,
        #    ),
        #)

        #self.saved_results[item.start] = page
        results = [self.searcher.doc(doc_id).to_dict() for (score, doc_id) in page.hits]
        return results


# class LocalDateParser(English):
#     def reverse_timezone_offset(self, d):
#         return (d.replace(tzinfo=django_timezone.get_current_timezone())).astimezone(
#             timezone.utc,
#         )

#     def date_from(self, *args, **kwargs):
#         d = super().date_from(*args, **kwargs)
#         if isinstance(d, timespan):
#             d.start = self.reverse_timezone_offset(d.start)
#             d.end = self.reverse_timezone_offset(d.end)
#         elif isinstance(d, datetime):
#             d = self.reverse_timezone_offset(d)
#         return d


class DelayedFullTextQuery(DelayedQuery):
    def _get_query(self):
        q_str = self.query_params["query"]
        index = open_index()
        q = index.parse_query(q_str, [
            "content",
            "title",
            "correspondent",
            "tag",
            "type",
            "notes",
            "custom_fields",
        ])
        # qp.add_plugin(
        #     DateParserPlugin(
        #         basedate=django_timezone.now(),
        #         dateparser=LocalDateParser(),
        #     ),
        # )

        # corrected = self.searcher.correct_query(q, q_str)
        # if corrected.query != q:
        #     corrected.query = corrected.string

        return q, None


# class DelayedMoreLikeThisQuery(DelayedQuery):
#     def _get_query(self):
#         more_like_doc_id = int(self.query_params["more_like_id"])
#         content = Document.objects.get(id=more_like_doc_id).content

#         docnum = self.searcher.document_number(id=more_like_doc_id)
#         kts = self.searcher.key_terms_from_text(
#             "content",
#             content,
#             numterms=20,
#             model=classify.Bo1Model,
#             normalize=False,
#         )
#         q = query.Or(
#             [query.Term("content", word, boost=weight) for word, weight in kts],
#         )
#         mask = {docnum}

#         return q, mask


def autocomplete(
    ix: tantivy.Index,
    term: str,
    limit: int = 10,
    user: Optional[User] = None,
):
    """
    Mimics whoosh.reading.IndexReader.most_distinctive_terms with permissions
    and without scoring
    """
    terms = []
    s = ix.searcher()
    q = ix.parse_query(term, ["content"])

    #user_criterias = get_permissions_criterias(user)

    results = s.search(
        query=q,
        #filter=query.Or(user_criterias) if user_criterias is not None else None,
    )

    print(results)
    termCounts = Counter()
    if results.has_matched_terms():
        for hit in results:
            for _, match in hit.matched_terms():
                termCounts[match] += 1
        terms = [t for t, _ in termCounts.most_common(limit)]

    term_encoded = term.encode("UTF-8")
    if term_encoded in terms:
        terms.insert(0, terms.pop(terms.index(term_encoded)))

    return terms


def get_permissions_criterias(user: Optional[User] = None):
    return [False]
    # user_criterias = [query.Term("has_owner", False)]
    # if user is not None:
    #     if user.is_superuser:  # superusers see all docs
    #         user_criterias = []
    #     else:
    #         user_criterias.append(query.Term("owner_id", user.id))
    #         user_criterias.append(
    #             query.Term("viewer_id", str(user.id)),
    #         )
    # return user_criterias
