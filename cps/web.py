#  This file is part of the Calibre-Web (https://github.com/janeczku/calibre-web)
#    Copyright (C) 2018-2019 OzzieIsaacs, cervinko, jkrehm, bodybybuddha, ok11,
#                            andy29485, idalin, Kyosfonica, wuqi, Kennyl, lemmsh,
#                            falgh1, grunjol, csitko, ytils, xybydy, trasba, vrabe,
#                            ruben-herold, marblepebble, JackED42, SiphonSquirrel,
#                            apetresc, nanu-c, mutschler
#
#  This program is free software: you can redistribute it and/or modify
#  it under the terms of the GNU General Public License as published by
#  the Free Software Foundation, either version 3 of the License, or
#  (at your option) any later version.
#
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#  GNU General Public License for more details.
#
#  You should have received a copy of the GNU General Public License
#  along with this program. If not, see <http://www.gnu.org/licenses/>.

import os
import json
import mimetypes
import chardet  # dependency of requests
import copy

from flask import Blueprint, Flask, jsonify
from flask import request, redirect, send_from_directory, make_response, flash, abort, url_for, Response, render_template
from flask import session as flask_session
from flask_babel import gettext as _
from flask_babel import get_locale
from flask_login import login_user, logout_user, login_required, current_user
from flask_limiter import RateLimitExceeded
from flask_limiter.util import get_remote_address
from sqlalchemy.exc import IntegrityError, InvalidRequestError, OperationalError
from sqlalchemy.sql.expression import text, func, false, not_, and_, or_
from sqlalchemy.orm.attributes import flag_modified
from sqlalchemy.sql.functions import coalesce

from flask import flash, redirect, url_for, request
import os
from . import web
from flask import jsonify
from .recomendador import *
from cps.db import Answer
from cps.ub import ForumCategory, Forum, Thread, Post

from werkzeug.datastructures import Headers
from werkzeug.security import generate_password_hash, check_password_hash

from . import constants, logger, isoLanguages, services
from . import db, ub, config, app
from cps.db import CalibreDB
from . import calibre_db, kobo_sync_status
from .search import render_search_results, render_adv_search_results
from .gdriveutils import getFileFromEbooksFolder, do_gdrive_download
from .helper import check_valid_domain, check_email, check_username, \
    get_book_cover, get_series_cover_thumbnail, get_download_link, send_mail, generate_random_password, \
    send_registration_mail, check_send_to_ereader, check_read_formats, tags_filters, reset_password, valid_email, \
    edit_book_read_status, valid_password
from .pagination import Pagination
from .redirect import get_redirect_location
from .babel import get_available_locale
from .usermanagement import login_required_if_no_ano
from .kobo_sync_status import remove_synced_book
from .render_template import render_title_template
from .kobo_sync_status import change_archived_books
from . import limiter
from .services.worker import WorkerThread
from .tasks_status import render_task_status


feature_support = {
    'ldap': bool(services.ldap),
    'goodreads': bool(services.goodreads_support),
    'kobo': bool(services.kobo)
}

try:
    from .oauth_bb import oauth_check, register_user_with_oauth, logout_oauth_user, get_oauth_status

    feature_support['oauth'] = True
except ImportError:
    feature_support['oauth'] = False
    oauth_check = {}
    register_user_with_oauth = logout_oauth_user = get_oauth_status = None

from functools import wraps

try:
    from natsort import natsorted as sort
except ImportError:
    sort = sorted  # Just use regular sort then, may cause issues with badly named pages in cbz/cbr files


@app.after_request
def add_security_headers(resp):
    default_src = ([host.strip() for host in config.config_trustedhosts.split(',') if host] +
                   ["'self'", "'unsafe-inline'", "'unsafe-eval'"])
    csp = "default-src " + ' '.join(default_src) + "; "
    csp += "font-src 'self' data:"
    if request.endpoint == "web.read_book":
        csp += " blob:"
    #csp += "; img-src 'self'"
    # Permitir imágenes del recomendador
    csp += "; img-src 'self' http://books.google.com https://books.google.com"
    if request.path.startswith("/author/") and config.config_use_goodreads:
        csp += " images.gr-assets.com i.gr-assets.com s.gr-assets.com"
    csp += " data:"
    if request.endpoint == "edit-book.show_edit_book" or config.config_use_google_drive:
        csp += " *;"
    elif request.endpoint == "web.read_book":
        csp += " blob:; style-src-elem 'self' blob: 'unsafe-inline';"
    else:
        csp += ";"
    csp += " object-src 'none';"
    resp.headers['Content-Security-Policy'] = csp
    resp.headers['X-Content-Type-Options'] = 'nosniff'
    resp.headers['X-Frame-Options'] = 'SAMEORIGIN'
    resp.headers['X-XSS-Protection'] = '1; mode=block'
    resp.headers['Strict-Transport-Security'] = 'max-age=31536000';
    return resp


web = Blueprint('web', __name__)

log = logger.create()


# ################################### Login logic and rights management ###############################################


def download_required(f):
    @wraps(f)
    def inner(*args, **kwargs):
        if current_user.role_download():
            return f(*args, **kwargs)
        abort(403)

    return inner


def viewer_required(f):
    @wraps(f)
    def inner(*args, **kwargs):
        if current_user.role_viewer():
            return f(*args, **kwargs)
        abort(403)

    return inner


# ################################### data provider functions #########################################################


@web.route("/ajax/emailstat")
@login_required
def get_email_status_json():
    tasks = WorkerThread.get_instance().tasks
    return jsonify(render_task_status(tasks))


@web.route("/ajax/bookmark/<int:book_id>/<book_format>", methods=['POST'])
@login_required
def set_bookmark(book_id, book_format):
    bookmark_key = request.form["bookmark"]
    ub.session.query(ub.Bookmark).filter(and_(ub.Bookmark.user_id == int(current_user.id),
                                              ub.Bookmark.book_id == book_id,
                                              ub.Bookmark.format == book_format)).delete()
    if not bookmark_key:
        ub.session_commit()
        return "", 204

    l_bookmark = ub.Bookmark(user_id=current_user.id,
                             book_id=book_id,
                             format=book_format,
                             bookmark_key=bookmark_key)
    ub.session.merge(l_bookmark)
    ub.session_commit("Bookmark for user {} in book {} created".format(current_user.id, book_id))
    return "", 201


@web.route("/ajax/toggleread/<int:book_id>", methods=['POST'])
@login_required
def toggle_read(book_id):
    message = edit_book_read_status(book_id)
    if message:
        return message, 400
    else:
        return message


@web.route("/ajax/togglearchived/<int:book_id>", methods=['POST'])
@login_required
def toggle_archived(book_id):
    is_archived = change_archived_books(book_id, message="Book {} archive bit toggled".format(book_id))
    if is_archived:
        remove_synced_book(book_id)
    return ""


@web.route("/ajax/view", methods=["POST"])
@login_required_if_no_ano
def update_view():
    to_save = request.get_json()
    try:
        for element in to_save:
            for param in to_save[element]:
                current_user.set_view_property(element, param, to_save[element][param])
    except Exception as ex:
        log.error("Could not save view_settings: %r %r: %e", request, to_save, ex)
        return "Invalid request", 400
    return "1", 200


'''
@web.route("/ajax/getcomic/<int:book_id>/<book_format>/<int:page>")
@login_required
def get_comic_book(book_id, book_format, page):
    book = calibre_db.get_book(book_id)
    if not book:
        return "", 204
    else:
        for bookformat in book.data:
            if bookformat.format.lower() == book_format.lower():
                cbr_file = os.path.join(config.config_calibre_dir, book.path, bookformat.name) + "." + book_format
                if book_format in ("cbr", "rar"):
                    if feature_support['rar'] == True:
                        rarfile.UNRAR_TOOL = config.config_rarfile_location
                        try:
                            rf = rarfile.RarFile(cbr_file)
                            names = sort(rf.namelist())
                            extract = lambda page: rf.read(names[page])
                        except:
                            # rarfile not valid
                            log.error('Unrar binary not found, or unable to decompress file %s', cbr_file)
                            return "", 204
                    else:
                        log.info('Unrar is not supported please install python rarfile extension')
                        # no support means return nothing
                        return "", 204
                elif book_format in ("cbz", "zip"):
                    zf = zipfile.ZipFile(cbr_file)
                    names=sort(zf.namelist())
                    extract = lambda page: zf.read(names[page])
                elif book_format in ("cbt", "tar"):
                    tf = tarfile.TarFile(cbr_file)
                    names=sort(tf.getnames())
                    extract = lambda page: tf.extractfile(names[page]).read()
                else:
                    log.error('unsupported comic format')
                    return "", 204

                b64 = codecs.encode(extract(page), 'base64').decode()
                ext = names[page].rpartition('.')[-1]
                if ext not in ('png', 'gif', 'jpg', 'jpeg', 'webp'):
                    ext = 'png'
                extractedfile="data:image/" + ext + ";base64," + b64
                fileData={"name": names[page], "page":page, "last":len(names)-1, "content": extractedfile}
                return make_response(json.dumps(fileData))
        return "", 204
'''


# ################################### Typeahead ##################################################################


@web.route("/get_authors_json", methods=['GET'])
@login_required_if_no_ano
def get_authors_json():
    return calibre_db.get_typeahead(db.Authors, request.args.get('q'), ('|', ','))


@web.route("/get_publishers_json", methods=['GET'])
@login_required_if_no_ano
def get_publishers_json():
    return calibre_db.get_typeahead(db.Publishers, request.args.get('q'), ('|', ','))


@web.route("/get_tags_json", methods=['GET'])
@login_required_if_no_ano
def get_tags_json():
    return calibre_db.get_typeahead(db.Tags, request.args.get('q'), tag_filter=tags_filters())


@web.route("/get_series_json", methods=['GET'])
@login_required_if_no_ano
def get_series_json():
    return calibre_db.get_typeahead(db.Series, request.args.get('q'))


@web.route("/get_languages_json", methods=['GET'])
@login_required_if_no_ano
def get_languages_json():
    query = (request.args.get('q') or '').lower()
    language_names = isoLanguages.get_language_names(get_locale())
    entries_start = [s for key, s in language_names.items() if s.lower().startswith(query.lower())]
    if len(entries_start) < 5:
        entries = [s for key, s in language_names.items() if query in s.lower()]
        entries_start.extend(entries[0:(5 - len(entries_start))])
        entries_start = list(set(entries_start))
    json_dumps = json.dumps([dict(name=r) for r in entries_start[0:5]])
    return json_dumps


@web.route("/get_matching_tags", methods=['GET'])
@login_required_if_no_ano
def get_matching_tags():
    tag_dict = {'tags': []}
    q = calibre_db.session.query(db.Books).filter(calibre_db.common_filters(True))
    calibre_db.session.connection().connection.connection.create_function("lower", 1, db.lcase)
    author_input = request.args.get('author_name') or ''
    title_input = request.args.get('book_title') or ''
    include_tag_inputs = request.args.getlist('include_tag') or ''
    exclude_tag_inputs = request.args.getlist('exclude_tag') or ''
    q = q.filter(db.Books.authors.any(func.lower(db.Authors.name).ilike("%" + author_input + "%")),
                 func.lower(db.Books.title).ilike("%" + title_input + "%"))
    if len(include_tag_inputs) > 0:
        for tag in include_tag_inputs:
            q = q.filter(db.Books.tags.any(db.Tags.id == tag))
    if len(exclude_tag_inputs) > 0:
        for tag in exclude_tag_inputs:
            q = q.filter(not_(db.Books.tags.any(db.Tags.id == tag)))
    for book in q:
        for tag in book.tags:
            if tag.id not in tag_dict['tags']:
                tag_dict['tags'].append(tag.id)
    json_dumps = json.dumps(tag_dict)
    return json_dumps


def generate_char_list(entries): # data_colum, db_link):
    char_list = list()
    for entry in entries:
        upper_char = entry[0].name[0].upper()
        if upper_char not in char_list:
            char_list.append(upper_char)
    return char_list


def query_char_list(data_colum, db_link):
    results = (calibre_db.session.query(func.upper(func.substr(data_colum, 1, 1)).label('char'))
            .join(db_link).join(db.Books).filter(calibre_db.common_filters())
            .group_by(func.upper(func.substr(data_colum, 1, 1))).all())
    return results


def get_sort_function(sort_param, data):
    order = [db.Books.timestamp.desc()]
    if sort_param == 'stored':
        sort_param = current_user.get_view_property(data, 'stored')
    else:
        current_user.set_view_property(data, 'stored', sort_param)
    if sort_param == 'pubnew':
        order = [db.Books.pubdate.desc()]
    if sort_param == 'pubold':
        order = [db.Books.pubdate]
    if sort_param == 'abc':
        order = [db.Books.sort]
    if sort_param == 'zyx':
        order = [db.Books.sort.desc()]
    if sort_param == 'new':
        order = [db.Books.timestamp.desc()]
    if sort_param == 'old':
        order = [db.Books.timestamp]
    if sort_param == 'authaz':
        order = [db.Books.author_sort.asc(), db.Series.name, db.Books.series_index]
    if sort_param == 'authza':
        order = [db.Books.author_sort.desc(), db.Series.name.desc(), db.Books.series_index.desc()]
    if sort_param == 'seriesasc':
        order = [db.Books.series_index.asc()]
    if sort_param == 'seriesdesc':
        order = [db.Books.series_index.desc()]
    if sort_param == 'hotdesc':
        order = [func.count(ub.Downloads.book_id).desc()]
    if sort_param == 'hotasc':
        order = [func.count(ub.Downloads.book_id).asc()]
    if sort_param is None:
        sort_param = "new"
    return order, sort_param


def render_books_list(data, sort_param, book_id, page):
    order = get_sort_function(sort_param, data)
    if data == "rated":
        return render_rated_books(page, book_id, order=order)
    elif data == "discover":
        return render_discover_books(book_id)
    elif data == "unread":
        return render_read_books(page, False, order=order)
    elif data == "read":
        return render_read_books(page, True, order=order)
    elif data == "hot":
        return render_hot_books(page, order)
    elif data == "download":
        return render_downloaded_books(page, order, book_id)
    elif data == "author":
        return render_author_books(page, book_id, order)
    elif data == "publisher":
        return render_publisher_books(page, book_id, order)
    elif data == "series":
        return render_series_books(page, book_id, order)
    elif data == "ratings":
        return render_ratings_books(page, book_id, order)
    elif data == "formats":
        return render_formats_books(page, book_id, order)
    elif data == "category":
        return render_category_books(page, book_id, order)
    elif data == "language":
        return render_language_books(page, book_id, order)
    elif data == "archived":
        return render_archived_books(page, order)
    elif data == "search":
        term = request.args.get('query', None)
        offset = int(int(config.config_books_per_page) * (page - 1))
        return render_search_results(term, offset, order, config.config_books_per_page)
    elif data == "advsearch":
        term = json.loads(flask_session.get('query', '{}'))
        offset = int(int(config.config_books_per_page) * (page - 1))
        return render_adv_search_results(term, offset, order, config.config_books_per_page)
    elif data == "recomendador":
        return render_recomendador(page, book_id, order)
    else:
        website = data or "newest"
        entries, random, pagination = calibre_db.fill_indexpage(page, 0, db.Books, True, order[0],
                                                                True, config.config_read_column,
                                                                db.books_series_link,
                                                                db.Books.id == db.books_series_link.c.book,
                                                                db.Series)
        return render_title_template('index.html', random=random, entries=entries, pagination=pagination,
                                     title=_("Books"), page=website, order=order[1])


def render_rated_books(page, book_id, order):
    if current_user.check_visibility(constants.SIDEBAR_BEST_RATED):
        entries, random, pagination = calibre_db.fill_indexpage(page, 0,
                                                                db.Books,
                                                                db.Books.ratings.any(db.Ratings.rating > 9),
                                                                order[0],
                                                                True, config.config_read_column,
                                                                db.books_series_link,
                                                                db.Books.id == db.books_series_link.c.book,
                                                                db.Series)

        return render_title_template('index.html', random=random, entries=entries, pagination=pagination,
                                     id=book_id, title=_("Top Rated Books"), page="rated", order=order[1])
    else:
        abort(404)


def render_discover_books(book_id):
    if current_user.check_visibility(constants.SIDEBAR_RANDOM):
        entries, __, ___ = calibre_db.fill_indexpage(1, 0, db.Books, True, [func.randomblob(2)],
                                                            join_archive_read=True,
                                                            config_read_column=config.config_read_column)
        pagination = Pagination(1, config.config_books_per_page, config.config_books_per_page)
        return render_title_template('index.html', random=false(), entries=entries, pagination=pagination, id=book_id,
                                     title=_("Discover (Random Books)"), page="discover")
    else:
        abort(404)


def render_hot_books(page, order):
    if current_user.check_visibility(constants.SIDEBAR_HOT):
        if order[1] not in ['hotasc', 'hotdesc']:
            # Unary expression comparison only working (for this expression) in sqlalchemy 1.4+
            # if not (order[0][0].compare(func.count(ub.Downloads.book_id).desc()) or
            #        order[0][0].compare(func.count(ub.Downloads.book_id).asc())):
            order = [func.count(ub.Downloads.book_id).desc()], 'hotdesc'
        if current_user.show_detail_random():
            random_query = calibre_db.generate_linked_query(config.config_read_column, db.Books)
            random = (random_query.filter(calibre_db.common_filters())
                     .order_by(func.random())
                     .limit(config.config_random_books).all())
        else:
            random = false()

        off = int(int(config.config_books_per_page) * (page - 1))
        all_books = ub.session.query(ub.Downloads, func.count(ub.Downloads.book_id)) \
            .order_by(*order[0]).group_by(ub.Downloads.book_id)
        hot_books = all_books.offset(off).limit(config.config_books_per_page)
        entries = list()
        for book in hot_books:
            query = calibre_db.generate_linked_query(config.config_read_column, db.Books)
            download_book = query.filter(calibre_db.common_filters()).filter(
                book.Downloads.book_id == db.Books.id).first()
            if download_book:
                entries.append(download_book)
            else:
                ub.delete_download(book.Downloads.book_id)
        num_books = entries.__len__()
        pagination = Pagination(page, config.config_books_per_page, num_books)
        return render_title_template('index.html', random=random, entries=entries, pagination=pagination,
                                     title=_("Hot Books (Most Downloaded)"), page="hot", order=order[1])
    else:
        abort(404)


def render_downloaded_books(page, order, user_id):
    if current_user.role_admin():
        user_id = int(user_id)
    else:
        user_id = current_user.id
    user = ub.session.query(ub.User).filter(ub.User.id == user_id).first()
    if current_user.check_visibility(constants.SIDEBAR_DOWNLOAD) and user:
        entries, random, pagination = calibre_db.fill_indexpage(page,
                                                            0,
                                                            db.Books,
                                                            ub.Downloads.user_id == user_id,
                                                            order[0],
                                                            True, config.config_read_column,
                                                            db.books_series_link,
                                                            db.Books.id == db.books_series_link.c.book,
                                                            db.Series,
                                                            ub.Downloads, db.Books.id == ub.Downloads.book_id)
        for book in entries:
            if not (calibre_db.session.query(db.Books).filter(calibre_db.common_filters())
                    .filter(db.Books.id == book.Books.id).first()):
                ub.delete_download(book.Books.id)
        return render_title_template('index.html',
                                     random=random,
                                     entries=entries,
                                     pagination=pagination,
                                     id=user_id,
                                     title=_("Downloaded books by %(user)s", user=user.name),
                                     page="download",
                                     order=order[1])
    else:
        abort(404)


def render_author_books(page, author_id, order):
    entries, __, pagination = calibre_db.fill_indexpage(page, 0,
                                                        db.Books,
                                                        db.Books.authors.any(db.Authors.id == author_id),
                                                        [order[0][0], db.Series.name, db.Books.series_index],
                                                        True, config.config_read_column,
                                                        db.books_series_link,
                                                        db.books_series_link.c.book == db.Books.id,
                                                        db.Series)
    if entries is None or not len(entries):
        flash(_("Oops! Selected book is unavailable. File does not exist or is not accessible"),
              category="error")
        return redirect(url_for("web.index"))
    if constants.sqlalchemy_version2:
        author = calibre_db.session.get(db.Authors, author_id)
    else:
        author = calibre_db.session.query(db.Authors).get(author_id)
    author_name = author.name.replace('|', ',')

    author_info = None
    other_books = []
    if services.goodreads_support and config.config_use_goodreads:
        author_info = services.goodreads_support.get_author_info(author_name)
        book_entries = [entry.Books for entry in entries]
        other_books = services.goodreads_support.get_other_books(author_info, book_entries)
    return render_title_template('author.html', entries=entries, pagination=pagination, id=author_id,
                                 title=_("Author: %(name)s", name=author_name), author=author_info,
                                 other_books=other_books, page="author", order=order[1])


def render_publisher_books(page, book_id, order):
    if book_id == '-1':
        entries, random, pagination = calibre_db.fill_indexpage(page, 0,
                                                                db.Books,
                                                                db.Publishers.name == None,
                                                                [db.Series.name, order[0][0], db.Books.series_index],
                                                                True, config.config_read_column,
                                                                db.books_publishers_link,
                                                                db.Books.id == db.books_publishers_link.c.book,
                                                                db.Publishers,
                                                                db.books_series_link,
                                                                db.Books.id == db.books_series_link.c.book,
                                                                db.Series)
        publisher = _("None")
    else:
        publisher = calibre_db.session.query(db.Publishers).filter(db.Publishers.id == book_id).first()
        if publisher:
            entries, random, pagination = calibre_db.fill_indexpage(page, 0,
                                                                    db.Books,
                                                                    db.Books.publishers.any(
                                                                        db.Publishers.id == book_id),
                                                                    [db.Series.name, order[0][0],
                                                                     db.Books.series_index],
                                                                    True, config.config_read_column,
                                                                    db.books_series_link,
                                                                    db.Books.id == db.books_series_link.c.book,
                                                                    db.Series)
            publisher = publisher.name
        else:
            abort(404)

    return render_title_template('index.html', random=random, entries=entries, pagination=pagination, id=book_id,
                                 title=_("Publisher: %(name)s", name=publisher),
                                 page="publisher",
                                 order=order[1])


def render_series_books(page, book_id, order):
    if book_id == '-1':
        entries, random, pagination = calibre_db.fill_indexpage(page, 0,
                                                                db.Books,
                                                                db.Series.name == None,
                                                                [order[0][0]],
                                                                True, config.config_read_column,
                                                                db.books_series_link,
                                                                db.Books.id == db.books_series_link.c.book,
                                                                db.Series)
        series_name = _("None")
    else:
        series_name = calibre_db.session.query(db.Series).filter(db.Series.id == book_id).first()
        if series_name:
            entries, random, pagination = calibre_db.fill_indexpage(page, 0,
                                                                    db.Books,
                                                                    db.Books.series.any(db.Series.id == book_id),
                                                                    [order[0][0]],
                                                                    True, config.config_read_column)
            series_name = series_name.name
        else:
            abort(404)
    return render_title_template('index.html', random=random, pagination=pagination, entries=entries, id=book_id,
                                 title=_("Series: %(serie)s", serie=series_name), page="series", order=order[1])


def render_ratings_books(page, book_id, order):
    if book_id == '-1':
        db_filter = coalesce(db.Ratings.rating, 0) < 1
        entries, random, pagination = calibre_db.fill_indexpage(page, 0,
                                                                db.Books,
                                                                db_filter,
                                                                [order[0][0]],
                                                                True, config.config_read_column,
                                                                db.books_ratings_link,
                                                                db.Books.id == db.books_ratings_link.c.book,
                                                                db.Ratings)
        title = _("Rating: None")
    else:
        name = calibre_db.session.query(db.Ratings).filter(db.Ratings.id == book_id).first()
        if name:
            entries, random, pagination = calibre_db.fill_indexpage(page, 0,
                                                                    db.Books,
                                                                    db.Books.ratings.any(db.Ratings.id == book_id),
                                                                    [order[0][0]],
                                                                    True, config.config_read_column)
            title = _("Rating: %(rating)s stars", rating=int(name.rating / 2))
        else:
            abort(404)
    return render_title_template('index.html', random=random, pagination=pagination, entries=entries, id=book_id,
                                 title=title, page="ratings", order=order[1])


def render_formats_books(page, book_id, order):
    if book_id == '-1':
        name = _("None")
        entries, random, pagination = calibre_db.fill_indexpage(page, 0,
                                                                db.Books,
                                                                db.Data.format == None,
                                                                [order[0][0]],
                                                                True, config.config_read_column,
                                                                db.Data)

    else:
        name = calibre_db.session.query(db.Data).filter(db.Data.format == book_id.upper()).first()
        if name:
            name = name.format
            entries, random, pagination = calibre_db.fill_indexpage(page, 0,
                                                                    db.Books,
                                                                    db.Books.data.any(
                                                                        db.Data.format == book_id.upper()),
                                                                    [order[0][0]],
                                                                    True, config.config_read_column)
        else:
            abort(404)

    return render_title_template('index.html', random=random, pagination=pagination, entries=entries, id=book_id,
                                 title=_("File format: %(format)s", format=name),
                                 page="formats",
                                 order=order[1])


def render_category_books(page, book_id, order):
    if book_id == '-1':
        entries, random, pagination = calibre_db.fill_indexpage(page, 0,
                                                                db.Books,
                                                                db.Tags.name == None,
                                                                [order[0][0], db.Series.name, db.Books.series_index],
                                                                True, config.config_read_column,
                                                                db.books_tags_link,
                                                                db.Books.id == db.books_tags_link.c.book,
                                                                db.Tags,
                                                                db.books_series_link,
                                                                db.Books.id == db.books_series_link.c.book,
                                                                db.Series)
        tagsname = _("None")
    else:
        tagsname = calibre_db.session.query(db.Tags).filter(db.Tags.id == book_id).first()
        if tagsname:
            entries, random, pagination = calibre_db.fill_indexpage(page, 0,
                                                                    db.Books,
                                                                    db.Books.tags.any(db.Tags.id == book_id),
                                                                    [order[0][0], db.Series.name,
                                                                     db.Books.series_index],
                                                                    True, config.config_read_column,
                                                                    db.books_series_link,
                                                                    db.Books.id == db.books_series_link.c.book,
                                                                    db.Series)
            tagsname = tagsname.name
        else:
            abort(404)
    return render_title_template('index.html', random=random, entries=entries, pagination=pagination, id=book_id,
                                 title=_("Category: %(name)s", name=tagsname), page="category", order=order[1])


def render_language_books(page, name, order):
    try:
        if name.lower() != "none":
            lang_name = isoLanguages.get_language_name(get_locale(), name)
            if lang_name == "Unknown":
                abort(404)
        else:
            lang_name = _("None")
    except KeyError:
        abort(404)
    if name == "none":
        entries, random, pagination = calibre_db.fill_indexpage(page, 0,
                                                                db.Books,
                                                                db.Languages.lang_code == None,
                                                                [order[0][0]],
                                                                True, config.config_read_column,
                                                                db.books_languages_link,
                                                                db.Books.id == db.books_languages_link.c.book,
                                                                db.Languages)
    else:
        entries, random, pagination = calibre_db.fill_indexpage(page, 0,
                                                                db.Books,
                                                                db.Books.languages.any(db.Languages.lang_code == name),
                                                                [order[0][0]],
                                                                True, config.config_read_column)
    return render_title_template('index.html', random=random, entries=entries, pagination=pagination, id=name,
                                 title=_("Language: %(name)s", name=lang_name), page="language", order=order[1])


def render_read_books(page, are_read, as_xml=False, order=None):
    sort_param = order[0] if order else []
    if not config.config_read_column:
        if are_read:
            db_filter = and_(ub.ReadBook.user_id == int(current_user.id),
                             ub.ReadBook.read_status == ub.ReadBook.STATUS_FINISHED)
        else:
            db_filter = coalesce(ub.ReadBook.read_status, 0) != ub.ReadBook.STATUS_FINISHED
    else:
        try:
            if are_read:
                db_filter = db.cc_classes[config.config_read_column].value == True
            else:
                db_filter = coalesce(db.cc_classes[config.config_read_column].value, False) != True
        except (KeyError, AttributeError, IndexError):
            log.error("Custom Column No.{} does not exist in calibre database".format(config.config_read_column))
            if not as_xml:
                flash(_("Custom Column No.%(column)d does not exist in calibre database",
                        column=config.config_read_column),
                      category="error")
                return redirect(url_for("web.index"))
            return []  # ToDo: Handle error Case for opds

    entries, random, pagination = calibre_db.fill_indexpage(page, 0,
                                                            db.Books,
                                                            db_filter,
                                                            sort_param,
                                                            True, config.config_read_column,
                                                            db.books_series_link,
                                                            db.Books.id == db.books_series_link.c.book,
                                                            db.Series)

    if as_xml:
        return entries, pagination
    else:
        if are_read:
            name = _('Read Books') + ' (' + str(pagination.total_count) + ')'
            page_name = "read"
        else:
            name = _('Unread Books') + ' (' + str(pagination.total_count) + ')'
            page_name = "unread"
        return render_title_template('index.html', random=random, entries=entries, pagination=pagination,
                                     title=name, page=page_name, order=order[1])


def render_archived_books(page, sort_param):
    order = sort_param[0] or []
    archived_books = (ub.session.query(ub.ArchivedBook)
                      .filter(ub.ArchivedBook.user_id == int(current_user.id))
                      .filter(ub.ArchivedBook.is_archived == True)
                      .all())
    archived_book_ids = [archived_book.book_id for archived_book in archived_books]

    archived_filter = db.Books.id.in_(archived_book_ids)

    entries, random, pagination = calibre_db.fill_indexpage_with_archived_books(page, db.Books,
                                                                                0,
                                                                                archived_filter,
                                                                                order,
                                                                                True,
                                                                                True, config.config_read_column)

    name = _('Archived Books') + ' (' + str(len(archived_book_ids)) + ')'
    page_name = "archived"
    return render_title_template('index.html', random=random, entries=entries, pagination=pagination,
                                 title=name, page=page_name, order=sort_param[1])


# ################################### View Books list ##################################################################


@web.route("/", defaults={'page': 1})
@web.route('/page/<int:page>')
@login_required_if_no_ano
def index(page):
    sort_param = (request.args.get('sort') or 'stored').lower()
    return render_books_list("newest", sort_param, 1, page)


@web.route('/<data>/<sort_param>', defaults={'page': 1, 'book_id': 1})
@web.route('/<data>/<sort_param>/', defaults={'page': 1, 'book_id': 1})
@web.route('/<data>/<sort_param>/<book_id>', defaults={'page': 1})
@web.route('/<data>/<sort_param>/<book_id>/<int:page>')
@login_required_if_no_ano
def books_list(data, sort_param, book_id, page):
    return render_books_list(data, sort_param, book_id, page)


@web.route("/table")
@login_required
def books_table():
    visibility = current_user.view_settings.get('table', {})
    cc = calibre_db.get_cc_columns(config, filter_config_custom_read=True)
    return render_title_template('book_table.html', title=_("Books List"), cc=cc, page="book_table",
                                 visiblility=visibility)


@web.route("/ajax/listbooks")
@login_required
def list_books():
    off = int(request.args.get("offset") or 0)
    limit = int(request.args.get("limit") or config.config_books_per_page)
    search_param = request.args.get("search")
    sort_param = request.args.get("sort", "id")
    order = request.args.get("order", "").lower()
    state = None
    join = tuple()

    if sort_param == "state":
        state = json.loads(request.args.get("state", "[]"))
    elif sort_param == "tags":
        order = [db.Tags.name.asc()] if order == "asc" else [db.Tags.name.desc()]
        join = db.books_tags_link, db.Books.id == db.books_tags_link.c.book, db.Tags
    elif sort_param == "series":
        order = [db.Series.name.asc()] if order == "asc" else [db.Series.name.desc()]
        join = db.books_series_link, db.Books.id == db.books_series_link.c.book, db.Series
    elif sort_param == "publishers":
        order = [db.Publishers.name.asc()] if order == "asc" else [db.Publishers.name.desc()]
        join = db.books_publishers_link, db.Books.id == db.books_publishers_link.c.book, db.Publishers
    elif sort_param == "authors":
        order = [db.Authors.name.asc(), db.Series.name, db.Books.series_index] if order == "asc" \
            else [db.Authors.name.desc(), db.Series.name.desc(), db.Books.series_index.desc()]
        join = db.books_authors_link, db.Books.id == db.books_authors_link.c.book, db.Authors, db.books_series_link, \
            db.Books.id == db.books_series_link.c.book, db.Series
    elif sort_param == "languages":
        order = [db.Languages.lang_code.asc()] if order == "asc" else [db.Languages.lang_code.desc()]
        join = db.books_languages_link, db.Books.id == db.books_languages_link.c.book, db.Languages
    elif order and sort_param in ["sort", "title", "authors_sort", "series_index"]:
        order = [text(sort_param + " " + order)]
    elif not state:
        order = [db.Books.timestamp.desc()]

    total_count = filtered_count = calibre_db.session.query(db.Books).filter(
        calibre_db.common_filters(allow_show_archived=True)).count()
    if state is not None:
        if search_param:
            books = calibre_db.search_query(search_param, config).all()
            filtered_count = len(books)
        else:
            query = calibre_db.generate_linked_query(config.config_read_column, db.Books)
            books = query.filter(calibre_db.common_filters(allow_show_archived=True)).all()
        entries = calibre_db.get_checkbox_sorted(books, state, off, limit, order, True)
    elif search_param:
        entries, filtered_count, __ = calibre_db.get_search_results(search_param,
                                                                    config,
                                                                    off,
                                                                    [order, ''],
                                                                    limit,
                                                                    *join)
    else:
        entries, __, __ = calibre_db.fill_indexpage_with_archived_books((int(off) / (int(limit)) + 1),
                                                                        db.Books,
                                                                        limit,
                                                                        True,
                                                                        order,
                                                                        True,
                                                                        True,
                                                                        config.config_read_column,
                                                                        *join)

    result = list()
    for entry in entries:
        val = entry[0]
        val.is_archived = entry[1] is True
        val.read_status = entry[2] == ub.ReadBook.STATUS_FINISHED
        for lang_index in range(0, len(val.languages)):
            val.languages[lang_index].language_name = isoLanguages.get_language_name(get_locale(), val.languages[
                lang_index].lang_code)
        result.append(val)

    table_entries = {'totalNotFiltered': total_count, 'total': filtered_count, "rows": result}
    js_list = json.dumps(table_entries, cls=db.AlchemyEncoder)

    response = make_response(js_list)
    response.headers["Content-Type"] = "application/json; charset=utf-8"
    return response


@web.route("/ajax/table_settings", methods=['POST'])
@login_required
def update_table_settings():
    current_user.view_settings['table'] = json.loads(request.data)
    try:
        try:
            flag_modified(current_user, "view_settings")
        except AttributeError:
            pass
        ub.session.commit()
    except (InvalidRequestError, OperationalError):
        log.error("Invalid request received: %r ", request, )
        return "Invalid request", 400
    return ""


@web.route("/author")
@login_required_if_no_ano
def author_list():
    if current_user.check_visibility(constants.SIDEBAR_AUTHOR):
        if current_user.get_view_property('author', 'dir') == 'desc':
            order = db.Authors.sort.desc()
            order_no = 0
        else:
            order = db.Authors.sort.asc()
            order_no = 1
        entries = calibre_db.session.query(db.Authors, func.count('books_authors_link.book').label('count')) \
            .join(db.books_authors_link).join(db.Books).filter(calibre_db.common_filters()) \
            .group_by(text('books_authors_link.author')).order_by(order).all()
        char_list = query_char_list(db.Authors.sort, db.books_authors_link)
        # If not creating a copy, readonly databases can not display authornames with "|" in it as changing the name
        # starts a change session
        author_copy = copy.deepcopy(entries)
        for entry in author_copy:
            entry.Authors.name = entry.Authors.name.replace('|', ',')
        return render_title_template('list.html', entries=author_copy, folder='web.books_list', charlist=char_list,
                                     title="Authors", page="authorlist", data='author', order=order_no)
    else:
        abort(404)


@web.route("/downloadlist")
@login_required_if_no_ano
def download_list():
    if current_user.get_view_property('download', 'dir') == 'desc':
        order = ub.User.name.desc()
        order_no = 0
    else:
        order = ub.User.name.asc()
        order_no = 1
    if current_user.check_visibility(constants.SIDEBAR_DOWNLOAD) and current_user.role_admin():
        entries = ub.session.query(ub.User, func.count(ub.Downloads.book_id).label('count')) \
            .join(ub.Downloads).group_by(ub.Downloads.user_id).order_by(order).all()
        char_list = ub.session.query(func.upper(func.substr(ub.User.name, 1, 1)).label('char')) \
            .filter(ub.User.role.op('&')(constants.ROLE_ANONYMOUS) != constants.ROLE_ANONYMOUS) \
            .group_by(func.upper(func.substr(ub.User.name, 1, 1))).all()
        return render_title_template('list.html', entries=entries, folder='web.books_list', charlist=char_list,
                                     title=_("Downloads"), page="downloadlist", data="download", order=order_no)
    else:
        abort(404)


@web.route("/publisher")
@login_required_if_no_ano
def publisher_list():
    if current_user.get_view_property('publisher', 'dir') == 'desc':
        order = db.Publishers.name.desc()
        order_no = 0
    else:
        order = db.Publishers.name.asc()
        order_no = 1
    if current_user.check_visibility(constants.SIDEBAR_PUBLISHER):
        entries = calibre_db.session.query(db.Publishers, func.count('books_publishers_link.book').label('count')) \
            .join(db.books_publishers_link).join(db.Books).filter(calibre_db.common_filters()) \
            .group_by(text('books_publishers_link.publisher')).order_by(order).all()
        no_publisher_count = (calibre_db.session.query(db.Books)
                           .outerjoin(db.books_publishers_link).outerjoin(db.Publishers)
                           .filter(db.Publishers.name == None)
                           .filter(calibre_db.common_filters())
                           .count())
        if no_publisher_count:
            entries.append([db.Category(_("None"), "-1"), no_publisher_count])
        entries = sorted(entries, key=lambda x: x[0].name.lower(), reverse=not order_no)
        char_list = generate_char_list(entries)
        return render_title_template('list.html', entries=entries, folder='web.books_list', charlist=char_list,
                                     title=_("Publishers"), page="publisherlist", data="publisher", order=order_no)
    else:
        abort(404)


@web.route("/series")
@login_required_if_no_ano
def series_list():
    if current_user.check_visibility(constants.SIDEBAR_SERIES):
        if current_user.get_view_property('series', 'dir') == 'desc':
            order = db.Series.sort.desc()
            order_no = 0
        else:
            order = db.Series.sort.asc()
            order_no = 1
        char_list = query_char_list(db.Series.sort, db.books_series_link)
        if current_user.get_view_property('series', 'series_view') == 'list':
            entries = calibre_db.session.query(db.Series, func.count('books_series_link.book').label('count')) \
                .join(db.books_series_link).join(db.Books).filter(calibre_db.common_filters()) \
                .group_by(text('books_series_link.series')).order_by(order).all()
            no_series_count = (calibre_db.session.query(db.Books)
                            .outerjoin(db.books_series_link).outerjoin(db.Series)
                            .filter(db.Series.name == None)
                            .filter(calibre_db.common_filters())
                            .count())
            if no_series_count:
                entries.append([db.Category(_("None"), "-1"), no_series_count])
            entries = sorted(entries, key=lambda x: x[0].name.lower(), reverse=not order_no)
            return render_title_template('list.html',
                                         entries=entries,
                                         folder='web.books_list',
                                         charlist=char_list,
                                         title=_("Series"),
                                         page="serieslist",
                                         data="series", order=order_no)
        else:
            entries = (calibre_db.session.query(db.Books, func.count('books_series_link').label('count'),
                                                func.max(db.Books.series_index), db.Books.id)
                       .join(db.books_series_link).join(db.Series).filter(calibre_db.common_filters())
                       .group_by(text('books_series_link.series'))
                       .having(or_(func.max(db.Books.series_index), db.Books.series_index==""))
                       .order_by(order)
                       .all())
            return render_title_template('grid.html', entries=entries, folder='web.books_list', charlist=char_list,
                                         title=_("Series"), page="serieslist", data="series", bodyClass="grid-view",
                                         order=order_no)
    else:
        abort(404)


@web.route("/ratings")
@login_required_if_no_ano
def ratings_list():
    if current_user.check_visibility(constants.SIDEBAR_RATING):
        if current_user.get_view_property('ratings', 'dir') == 'desc':
            order = db.Ratings.rating.desc()
            order_no = 0
        else:
            order = db.Ratings.rating.asc()
            order_no = 1
        entries = calibre_db.session.query(db.Ratings, func.count('books_ratings_link.book').label('count'),
                                           (db.Ratings.rating / 2).label('name')) \
            .join(db.books_ratings_link).join(db.Books).filter(calibre_db.common_filters()) \
            .filter(db.Ratings.rating > 0) \
            .group_by(text('books_ratings_link.rating')).order_by(order).all()
        no_rating_count = (calibre_db.session.query(db.Books)
                           .outerjoin(db.books_ratings_link).outerjoin(db.Ratings)
                           .filter(or_(db.Ratings.rating == None, db.Ratings.rating == 0))
                           .filter(calibre_db.common_filters())
                           .count())
        if no_rating_count:
            entries.append([db.Category(_("None"), "-1", -1), no_rating_count])
        entries = sorted(entries, key=lambda x: x[0].rating, reverse=not order_no)
        return render_title_template('list.html', entries=entries, folder='web.books_list', charlist=list(),
                                     title=_("Ratings list"), page="ratingslist", data="ratings", order=order_no)
    else:
        abort(404)


@web.route("/formats")
@login_required_if_no_ano
def formats_list():
    if current_user.check_visibility(constants.SIDEBAR_FORMAT):
        if current_user.get_view_property('formats', 'dir') == 'desc':
            order = db.Data.format.desc()
            order_no = 0
        else:
            order = db.Data.format.asc()
            order_no = 1
        entries = calibre_db.session.query(db.Data,
                                           func.count('data.book').label('count'),
                                           db.Data.format.label('format')) \
            .join(db.Books).filter(calibre_db.common_filters()) \
            .group_by(db.Data.format).order_by(order).all()
        no_format_count = (calibre_db.session.query(db.Books).outerjoin(db.Data)
                           .filter(db.Data.format == None)
                           .filter(calibre_db.common_filters())
                           .count())
        if no_format_count:
            entries.append([db.Category(_("None"), "-1"), no_format_count])
        return render_title_template('list.html', entries=entries, folder='web.books_list', charlist=list(),
                                     title=_("File formats list"), page="formatslist", data="formats", order=order_no)
    else:
        abort(404)


@web.route("/language")
@login_required_if_no_ano
def language_overview():
    if current_user.check_visibility(constants.SIDEBAR_LANGUAGE) and current_user.filter_language() == "all":
        order_no = 0 if current_user.get_view_property('language', 'dir') == 'desc' else 1
        languages = calibre_db.speaking_language(reverse_order=not order_no, with_count=True)
        char_list = generate_char_list(languages)
        return render_title_template('list.html', entries=languages, folder='web.books_list', charlist=char_list,
                                     title=_("Languages"), page="langlist", data="language", order=order_no)
    else:
        abort(404)


@web.route("/category")
@login_required_if_no_ano
def category_list():
    if current_user.check_visibility(constants.SIDEBAR_CATEGORY):
        if current_user.get_view_property('category', 'dir') == 'desc':
            order = db.Tags.name.desc()
            order_no = 0
        else:
            order = db.Tags.name.asc()
            order_no = 1
        entries = calibre_db.session.query(db.Tags, func.count('books_tags_link.book').label('count')) \
            .join(db.books_tags_link).join(db.Books).order_by(order).filter(calibre_db.common_filters()) \
            .group_by(db.Tags.id).all()
        no_tag_count = (calibre_db.session.query(db.Books)
                         .outerjoin(db.books_tags_link).outerjoin(db.Tags)
                        .filter(db.Tags.name == None)
                         .filter(calibre_db.common_filters())
                         .count())
        if no_tag_count:
            entries.append([db.Category(_("None"), "-1"), no_tag_count])
        entries = sorted(entries, key=lambda x: x[0].name.lower(), reverse=not order_no)
        char_list = generate_char_list(entries)
        return render_title_template('list.html', entries=entries, folder='web.books_list', charlist=char_list,
                                     title=_("Categories"), page="catlist", data="category", order=order_no)
    else:
        abort(404)




# ################################### Download/Send ##################################################################


@web.route("/cover/<int:book_id>")
@web.route("/cover/<int:book_id>/<string:resolution>")
@login_required_if_no_ano
def get_cover(book_id, resolution=None):
    resolutions = {
        'og': constants.COVER_THUMBNAIL_ORIGINAL,
        'sm': constants.COVER_THUMBNAIL_SMALL,
        'md': constants.COVER_THUMBNAIL_MEDIUM,
        'lg': constants.COVER_THUMBNAIL_LARGE,
    }
    cover_resolution = resolutions.get(resolution, None)
    return get_book_cover(book_id, cover_resolution)


@web.route("/series_cover/<int:series_id>")
@web.route("/series_cover/<int:series_id>/<string:resolution>")
@login_required_if_no_ano
def get_series_cover(series_id, resolution=None):
    resolutions = {
        'og': constants.COVER_THUMBNAIL_ORIGINAL,
        'sm': constants.COVER_THUMBNAIL_SMALL,
        'md': constants.COVER_THUMBNAIL_MEDIUM,
        'lg': constants.COVER_THUMBNAIL_LARGE,
    }
    cover_resolution = resolutions.get(resolution, None)
    return get_series_cover_thumbnail(series_id, cover_resolution)



@web.route("/robots.txt")
def get_robots():
    try:
        return send_from_directory(constants.STATIC_DIR, "robots.txt")
    except PermissionError:
        log.error("No permission to access robots.txt file.")
        abort(403)


@web.route("/show/<int:book_id>/<book_format>", defaults={'anyname': 'None'})
@web.route("/show/<int:book_id>/<book_format>/<anyname>")
@login_required_if_no_ano
@viewer_required
def serve_book(book_id, book_format, anyname):
    book_format = book_format.split(".")[0]
    book = calibre_db.get_book(book_id)
    data = calibre_db.get_book_format(book_id, book_format.upper())
    if not data:
        return "File not in Database"
    range_header = request.headers.get('Range', None)

    if config.config_use_google_drive:
        try:
            headers = Headers()
            headers["Content-Type"] = mimetypes.types_map.get('.' + book_format, "application/octet-stream")
            if not range_header:
                log.info('Serving book: %s', data.name)
                headers['Accept-Ranges'] = 'bytes'
            df = getFileFromEbooksFolder(book.path, data.name + "." + book_format)
            return do_gdrive_download(df, headers, (book_format.upper() == 'TXT'))
        except AttributeError as ex:
            log.error_or_exception(ex)
            return "File Not Found"
    else:
        if book_format.upper() == 'TXT':
            log.info('Serving book: %s', data.name)
            try:
                rawdata = open(os.path.join(config.get_book_path(), book.path, data.name + "." + book_format),
                               "rb").read()
                result = chardet.detect(rawdata)
                try:
                    text_data = rawdata.decode(result['encoding']).encode('utf-8')
                except UnicodeDecodeError as e:
                    log.error("Encoding error in text file {}: {}".format(book.id, e))
                    if "surrogate" in e.reason:
                        text_data = rawdata.decode(result['encoding'], 'surrogatepass').encode('utf-8', 'surrogatepass')
                    else:
                        text_data = rawdata.decode(result['encoding'], 'ignore').encode('utf-8', 'ignore')
                return make_response(text_data)
            except FileNotFoundError:
                log.error("File Not Found")
                return "File Not Found"
        # enable byte range read of pdf
        response = make_response(
            send_from_directory(os.path.join(config.get_book_path(), book.path), data.name + "." + book_format))
        if not range_header:
            log.info('Serving book: %s', data.name)
            response.headers['Accept-Ranges'] = 'bytes'
        return response


@web.route("/download/<int:book_id>/<book_format>", defaults={'anyname': 'None'})
@web.route("/download/<int:book_id>/<book_format>/<anyname>")
@login_required_if_no_ano
@download_required
def download_link(book_id, book_format, anyname):
    client = "kobo" if "Kobo" in request.headers.get('User-Agent') else ""
    return get_download_link(book_id, book_format, client)

#################################### RECOMENDADOR ####################################
def render_recomendador(page, book_id=None, order=['default', 'order'], result=None):
    if order is None:
        order = ['default', 'order']
        
    return render_title_template(
        'recomendador.html',
        questions=questions,
        result=result,
        title=_("Recomendador"),
        page="recomendador",
        order=order[1]
    )

@app.route('/recomendador', methods=['GET', 'POST'])
@login_required
def recomendador():
    user_id = current_user.id
    recommendation = ub.session.query(ub.Recommendation).filter_by(user_id=user_id).first()

    if recommendation and not request.args.get('reset'):
        return redirect(url_for('recomendaciones'))
    
    if request.method == 'POST':
        return redirect(url_for('recomendaciones'))
    
    # Si reset está a True, borrar las respuestas y la recomendación
    if request.args.get('reset'):
        ub.session.query(ub.Answer).filter_by(user_id=user_id).delete()
        ub.session.query(ub.Recommendation).filter_by(user_id=user_id).delete()
        ub.session.commit()
        
    return render_recomendador(page=1, book_id=None, order=['default', 'order'])
   


@app.route('/recomendaciones', methods=['GET', 'POST'])
@login_required
def recomendaciones():
    user_id = current_user.id

    if request.method == 'POST':
        answers = []
        try:
            for question_id in questions.keys():
                answer = request.form.get(f'question_{question_id}')
                if answer:
                    answer = float(answer)
                    
                    # Verificar si hay respuestas guardadas
                    existing_answer = ub.session.query(ub.Answer).filter_by(user_id=user_id, question_id=question_id).first()
                    if existing_answer:
                        existing_answer.answer = answer # Actualizar la respuesta existente
                    else:
                        # Crear una nueva respuesta
                        new_answer = ub.Answer(user_id=user_id, question_id=question_id, answer=answer)
                        answers.append(new_answer)
                    
            # Guardar las respuestas en la base de datos
            if answers:
                ub.session.bulk_save_objects(answers)
            ub.session.commit()

        except ValueError:
            flash("Hubo un error al procesar sus respuestas. Asegúrese de que todas las respuestas son válidas.", category="error")
        except IntegrityError:
            ub.session.rollback()
            flash("Hubo un error de integridad en la base de datos.", category="error")
        except OperationalError as e:
            ub.session.rollback()
            flash(f"Error operativo de la base de datos: {e}", category="error")

        # Obtener las respuestas guardadas del usuario
        user_answers = ub.session.query(ub.Answer).filter_by(user_id=user_id).all()
        
        # Calcular las probabilidades y determinar el género recomendado
        questions_so_far = [ans.question_id for ans in user_answers]
        answers_so_far = [ans.answer for ans in user_answers]
        probabilities = calculate_probabilities(questions_so_far, answers_so_far)
        result_name = sorted(probabilities, key=lambda p: p['probability'], reverse=True)[0]['name']
        recommended_books_names = sorted(probabilities, key=lambda p: p['probability'], reverse=True)[0]['name'].split(', ')
        
        result = []
        for book_name in recommended_books_names:
            print(f"DEBUG: Obteniendo información del libro: {book_name}")  # DEBUG
            book_info = get_book_info(title=book_name)
            if book_info:
                result.append({
                    'title': book_info['title'],
                    'author': book_info['author'],
                    'isbn': book_info['isbn'],
                    'description': book_info['description'],
                    'publishedDate': book_info['publication_info'],
                    'pageCount': book_info['page_count'],
                    'language': book_info['language'],
                    'thumbnail': book_info['thumbnail']
                })
        
        # Guardar recomendación
        existing_recommendation = ub.session.query(ub.Recommendation).filter_by(user_id=user_id).first()
        if existing_recommendation:
            existing_recommendation.result = result
        else:
            new_recommendation = ub.Recommendation(user_id=user_id, result=result)
            ub.session.add(new_recommendation)
            
        ub.session.commit()

    else:
        # Si es GET, cargar la recomendación existente
        existing_recommendation = ub.session.query(ub.Recommendation).filter_by(user_id=user_id).first()
        if existing_recommendation:
            result = existing_recommendation.result
        else:
            flash("No hay recomendaciones disponibles.", category="error")
            return redirect(url_for('recomendador'))

    return render_title_template(
        'detail_recomendador.html',
        result=result,
        title="Recomendador")


# Buscar libro recomendador
import requests

def get_book_info(isbn=None, title=None):
    base_url = 'https://www.googleapis.com/books/v1/volumes'
    params = {}

    if isbn:
        params['q'] = f'isbn:{isbn}'
    elif title:
        params['q'] = f'intitle:{title}'
    else:
        raise ValueError("El ISBN o el título deben ser proporcionados.")

    params['key'] = 'AIzaSyACIkFe67iKjQSMS01E3BPiftD9AAs8QAU'
    
    response = requests.get(base_url, params=params)
    response.raise_for_status()
    data = response.json()

    items = data.get('items')
    if not items:
        return None
    
    book = items[0]['volumeInfo']
    title = book.get('title', 'Desconocido')
    author = ', '.join(book.get('authors', ['Desconocido']))
    publication_info = book.get('publishedDate', 'Desconocido')
    description = book.get('description', 'No hay descripción disponible.')
    page_count = book.get('pageCount', 'Desconocido')
    language = book.get('language', 'Desconocido')
    thumbnail = book.get('imageLinks', {}).get('thumbnail', 'https://via.placeholder.com/150x220?text=No+Image')

    # Obtener ISBN
    industry_identifiers = book.get('industryIdentifiers', [])
    isbn = 'N/A'
    for identifier in industry_identifiers:
        if identifier['type'] in ['ISBN_10', 'ISBN_13']:
            isbn = identifier['identifier']
            break
        
    book_info = {
        'title': title,
        'author': author,
        'isbn': isbn,
        'publication_info': publication_info,
        'description': description,
        'page_count': page_count,
        'language': language,
        'thumbnail': thumbnail
    }

    return book_info

#################################### RECOMENDADOR ####################################

################################ FORO ################################

# Ruta para eliminar un foro
@app.route('/forums/delete/<int:forum_id>', methods=['POST'])
@login_required
def delete_forum(forum_id):
    if not current_user.role_admin():
        flash('¡Acceso no autorizado!', 'danger')
        return redirect(url_for('manage_forums'))
    
    forum = ub.session.query(ub.Forum).get(forum_id)
    if not forum:
        flash('¡Foro no encontrado!', 'danger')
        return redirect(url_for('manage_forums'))
    
    try:
        
        # Eliminar todos los hilos asociados con el foro
        threads = ub.session.query(ub.Thread).filter_by(forum_id=forum_id).all()
        for thread in threads:
            # Eliminar todos los posts asociados con el hilo
            posts = ub.session.query(ub.Post).filter_by(thread_id=thread.id).all()
            for post in posts:
                ub.session.delete(post)
            
            ub.session.delete(thread)
            
        ub.session.delete(forum)
        ub.session.commit()
        flash('¡Foro eliminado con éxito!', 'success')
    except Exception as e:
        ub.session.rollback()
        flash('Ha ocurrido un error al borrar el foro. Por favor, inténtelo de nuevo.', 'danger')
    
    return redirect(url_for('manage_forums'))

# Ruta para eliminar un hilo
@app.route('/threads/delete/<int:thread_id>', methods=['POST'])
@login_required
def delete_thread(thread_id):
    if not current_user.role_admin():
        flash('No tienes permiso para borrar hilos.', 'danger')
        return redirect(url_for('view_forum'))
    
    thread = ub.session.query(ub.Thread).get(thread_id)
    if not thread:
        flash('¡Hilo no encontrado!', 'danger')
        return redirect(url_for('view_forum'))
    
    try:
        # Eliminar todos los posts asociados con el thread
        posts = ub.session.query(ub.Post).filter_by(thread_id=thread_id).all()
        for post in posts:
            ub.session.delete(post)
        
        ub.session.delete(thread)
        ub.session.commit()
        flash('¡El hilo y sus comentarios han sido eliminados con éxito!', 'success')
    except Exception as e:
        ub.session.rollback()
        flash('Ha ocurrido un error al borrar el hilo. Por favor, inténtelo de nuevo.', 'danger')
    
    return redirect(url_for('view_forum', forum_id=thread.forum_id))

# Ruta para eliminar una publicación
@app.route('/posts/delete/<int:post_id>', methods=['POST'])
@login_required
def delete_post(post_id):
    if not current_user.role_admin():
        flash('¡Acceso no autorizado!', 'danger')
        return redirect(url_for('manage_posts'))
    
    post = ub.session.query(ub.Post).get(post_id)
    if not post:
        flash('¡Comentario no encontrado!', 'danger')
        return redirect(url_for('manage_posts'))
    
    thread_id = post.thread_id
    try:
        ub.session.delete(post)
        ub.session.commit()
        flash('¡Comentario eliminado con éxito!', 'success')
    except Exception as e:
        ub.session.rollback()
        flash('Ha ocurrido un error al borrar el comentario. Por favor, inténtelo de nuevo.', 'danger')
    
    return redirect(url_for('view_thread', thread_id=thread_id))

@app.route('/categories/create_default', methods=['POST'])
@login_required
def create_default_category():
    default_category = ub.session.query(ub.ForumCategory).filter_by(name='Sin Categoría').first()
    if default_category:
        return default_category

    try:
        new_category = ub.ForumCategory(name='Sin Categoría', description='Categoría por defecto para foros no asignados.')
        ub.session.add(new_category)
        ub.session.commit()
        return new_category
    except Exception as e:
        ub.session.rollback()
        raise Exception('Ha ocurrido un error al crear la categoría por defecto. Por favor, inténtelo de nuevo.')

# Ruta para eliminar una categoría
@app.route('/categories/delete/<int:category_id>', methods=['POST'])
@login_required
def delete_category(category_id):
    if not current_user.role_admin():
        flash('¡Acceso no autorizado!', 'danger')
        return redirect(url_for('manage_categories'))
    
    category = ub.session.query(ub.ForumCategory).get(category_id)
    if not category:
        flash('¡Categoría no encontrada!', 'danger')
        return redirect(url_for('manage_categories'))

    # Obtener o crear la categoría por defecto
    default_category = ub.session.query(ub.ForumCategory).filter_by(name='Sin Categoría').first()
    if not default_category:
        try:
            create_default_category()
            default_category = ub.session.query(ub.ForumCategory).filter_by(name='Sin Categoría').first()
        except Exception as e:
            flash('Ha ocurrido un error al crear la categoría por defecto. Por favor, inténtelo de nuevo.', 'danger')
            return redirect(url_for('manage_categories'))

    try:
        # Actualizar los foros que usan esta categoría
        affected_forums = ub.session.query(ub.Forum).filter_by(category_id=category_id).update({'category_id': default_category.id})
        
        ub.session.delete(category)
        ub.session.commit()
        
        if affected_forums:
            flash('¡Categoría eliminada con éxito y foros afectados actualizados!', 'success')
        else:
            flash('¡Categoría eliminada con éxito!', 'success')
    except Exception as e:
        ub.session.rollback()
        flash('Ha ocurrido un error al borrar la categoría. Por favor, inténtelo de nuevo.', 'danger')
    
    return redirect(url_for('manage_categories'))


@app.route('/categories')
@login_required
def manage_categories():
    categories = ub.session.query(ub.ForumCategory).all()
    return render_title_template(
        'manage_categories.html',
        categories=categories,
        title="Borrar Categorías",
        page='forum'
    )

@app.route('/categories/add', methods=['GET', 'POST'])
@login_required
def add_category():
    if request.method == 'POST':
        name = request.form.get('name')
        description = request.form.get('description', '')

        if not name:
            flash('¡Nombre requerido!', 'danger')
            return render_title_template('add_category.html', title="Categories", page='forum', error_name=name)

        existing_category = ub.session.query(ub.ForumCategory).filter_by(name=name).first()
        if existing_category:
            flash('Ya existe una categoría con ese nombre. Por favor, elige un nombre diferente.', 'danger')
            return render_title_template(
                'add_category.html',
                title="Categories",
                page='forum',
                error_name=name
            )

        try:
            category = ub.ForumCategory(name=name, description=description)
            ub.session.add(category)
            ub.session.commit()
            flash('¡Categoría agregada con éxito!', 'success')
        except Exception as e:
            flash('Ha ocurrido un error al agregar la categoría. Por favor, inténtelo de nuevo.', 'danger')
            return render_title_template('add_category.html', title="Categories", page='forum')
        
        return redirect(url_for('manage_forums'))

    return render_title_template('add_category.html', title="Categories", page='forum')


@app.route('/forums')
@login_required
def manage_forums():
    forums = ub.session.query(ub.Forum).all()
    return render_title_template(
        'manage_forums.html',
        forums=forums,
        title="Forum",
        page='forum'
    )
    
@app.route('/forums/add', methods=['GET', 'POST'])
@login_required
def add_forum():
    if request.method == 'POST':
        name = request.form.get('name')
        description = request.form.get('description', '')
        category_id = request.form.get('category_id')
        category_id = int(category_id)

        existing_forum = ub.session.query(ub.Forum).filter_by(name=name).first()
        if existing_forum:
            flash('Ya existe un foro con ese nombre. Por favor, elige un nombre diferente.', 'danger')
            categories = ub.session.query(ub.ForumCategory).all()
            return render_title_template(
                'add_forum.html',
                categories=categories,
                title="Forum",
                page='forum',
                error_name=name
            )

        try:
            forum = ub.Forum(name=name, description=description, category_id=category_id)
            ub.session.add(forum)
            ub.session.commit()
            flash('¡Foro agregado con éxito!', 'success')
        except Exception as e:
            flash('Ha ocurrido un error al agregar el foro. Por favor, inténtelo de nuevo.', 'danger')
            categories = ub.session.query(ub.ForumCategory).all()
            return render_title_template(
                'add_forum.html',
                categories=categories,
                title="Forum",
                page='forum',
                error_name=name
            )
        return redirect(url_for('manage_forums'))

    categories = ub.session.query(ub.ForumCategory).all()
    return render_title_template(
        'add_forum.html',
        categories=categories,
        title="Forum",
        page='forum'
    )


@app.route('/forums/edit/<int:forum_id>', methods=['GET', 'POST'])
@login_required
def edit_forum(forum_id):
    forum = ub.session.query(ub.Forum).get(forum_id)
    if not forum:
        flash('¡Foro no encontrado!', 'danger')
        return redirect(url_for('manage_forums'))
    
    if request.method == 'POST':
        new_name = request.form.get('name')
        new_description = request.form.get('description', '')
        new_category_id = int(request.form.get('category_id'))
        
        # Verificar si el nuevo nombre ya existe
        existing_forum = ub.session.query(ub.Forum).filter_by(name=new_name).first()
        
        if existing_forum and existing_forum.id != forum_id:
            flash('Ya existe un foro con ese nombre. Elija otro nombre.', 'danger')
            return render_template(
                'edit_forum.html',
                forum=forum,
                categories=ub.session.query(ub.ForumCategory).all(),
                title="Edit Forum",
                page='forum'
            )
        
        try:
            forum.name = new_name
            forum.description = new_description
            forum.category_id = new_category_id
            
            ub.session.commit()
            flash('¡Foro actualizado con éxito!', 'success')
            return redirect(url_for('manage_forums'))
        except Exception as e:
            ub.session.rollback()
            flash('Ha ocurrido un error al actualizar el foro. Por favor, inténtelo de nuevo.', 'danger')
    
    categories = ub.session.query(ub.ForumCategory).all()
    return render_template(
        'edit_forum.html',
        forum=forum,
        categories=categories,
        title="Edit Forum",
        page='forum'
    )


# Ver hilos en un foro específico
@app.route('/forums/<int:forum_id>')
@login_required
def view_forum(forum_id):
    forum = ub.session.query(ub.Forum).get(forum_id)
    if not forum:
        flash('¡Foro no encontrado!', 'danger')
        return redirect(url_for('manage_forums'))
    threads = ub.session.query(ub.Thread).filter_by(forum_id=forum_id).all()
    return render_title_template(
        'view_forum.html',
        forum=forum,
        threads=threads,
        title=forum.name,
        page='forum'
    )


@app.route('/threads')
@login_required
def manage_threads():
    threads = ub.session.query(ub.Thread).all()
    return render_title_template(
        'manage_threads.html',
        threads=threads,
        title="Threads",
        page='forum'
    )
    
@app.route('/forums/<int:forum_id>/threads/add', methods=['GET', 'POST'])
@login_required
def add_thread(forum_id):
    forum = ub.session.query(ub.Forum).get(forum_id)
    if not forum:
        flash('¡Foro no encontrado!', 'danger')
        return redirect(url_for('manage_forums'))
    
    if request.method == 'POST':
        title = request.form['title']
        content = request.form['content']
        user_id = current_user.id        
        
        thread = ub.Thread(title=title, forum_id=forum_id, user_id=user_id)
        ub.session.add(thread)
        ub.session.commit()
        
        post = ub.Post(content=content, thread_id=thread.id, user_id=user_id)
        ub.session.add(post)
        ub.session.commit()
        
        flash('¡Hilo y comentario añadidos con éxito!', 'success')
        return redirect(url_for('view_forum', forum_id=forum_id))
    
    
    return render_title_template(
        'add_thread.html',
        forum=forum,
        forum_id=forum_id,
        title="Threads",
        page = 'forum'
    )

# Ver posts en un hilo específico
@app.route('/threads/<int:thread_id>')
@login_required
def view_thread(thread_id):
    thread = ub.session.query(ub.Thread).get(thread_id)
    if not thread:
        flash('¡Hilo no encontrado!', 'danger')
        return redirect(url_for('view_forum'))
    
    posts = ub.session.query(ub.Post).filter_by(thread_id=thread_id).order_by(ub.Post.created_at).all()
    
    return render_title_template(
        'view_thread.html',
        thread=thread,
        posts=posts,
        title=thread.title,
        page='forum'
    )

@app.route('/posts', defaults={'thread_id': None})
@app.route('/posts/<int:thread_id>')
@login_required
def manage_posts(thread_id):
    posts_query = ub.session.query(ub.Post)
    
    if thread_id:
        posts_query = posts_query.filter_by(thread_id=thread_id)
    
    posts = posts_query.all()
    return render_title_template(
        'manage_posts.html',
        posts=posts,
        thread_id=thread_id,
        title="Posts",
        page='forum'
    )
    
@app.route('/posts/add/<int:thread_id>', methods=['GET', 'POST'])
@login_required
def add_post(thread_id):
    thread = ub.session.query(ub.Thread).get(thread_id)
    if not thread:
        flash('¡Hilo no encontrado!', 'danger')
        return redirect(url_for('view_forum'))

    if request.method == 'POST':
        content = request.form['content']
        user_id = current_user.id
        post = ub.Post(content=content, thread_id=thread_id, user_id=user_id)
        ub.session.add(post)
        ub.session.commit()
        
        # Crear notificaciones para los seguidores
        followers = ub.session.query(ub.User).join(ub.UserFollow, ub.User.id == ub.UserFollow.follower_id).filter(ub.UserFollow.followed_id == user_id).all()
        for follower in followers:
            notification = ub.Notification(
                user_id=follower.id,
                message=f'El usuario {current_user.name} ha publicado en el foro.',
                post_id=post.id
            )
            ub.session.add(notification)
        
        ub.session.commit()
        
        flash('¡Post agregado con éxito!', 'success')
        return redirect(url_for('view_thread', thread_id=thread_id))
    
    return render_title_template(
        'add_post.html',
        thread=thread,
        title="Posts",
        page='forum'
    )

################################ FORO ################################

################################ RED SOCIAL ################################
@app.route('/chat/<int:user_id>', methods=['GET', 'POST'])
@login_required
def chat(user_id):
    other_user = ub.session.query(ub.User).get(user_id)
    if not other_user or other_user.id == current_user.id:
        flash('Usuario no encontrado o no puedes chatear contigo mismo.', 'danger')
        return redirect(url_for('user_profile', user_id=current_user.id))

    if request.method == 'POST':
        content = request.form.get('content')
        if content:
            message = ub.Message(sender_id=current_user.id, receiver_id=other_user.id, content=content)
            ub.session.add(message)
            
            # Crear una notificación para el receptor del mensaje
            notification_message = f"Has recibido un mensaje de {current_user.name}"
            notification = ub.Notification(user_id=other_user.id, message=notification_message)
            ub.session.add(notification)
            
            ub.session.commit()
            flash('¡Mensaje enviado!', 'success')
            return redirect(url_for('chat', user_id=user_id))

    # Obtener mensajes entre el usuario actual y el usuario con el que se chatea
    messages = ub.session.query(ub.Message).filter(
        ((ub.Message.sender_id == current_user.id) & (ub.Message.receiver_id == other_user.id)) |
        ((ub.Message.sender_id == other_user.id) & (ub.Message.receiver_id == current_user.id))
    ).order_by(ub.Message.timestamp).all()

    return render_title_template('chat.html', user=other_user, messages=messages, title="Chat", page='chat')

@app.route('/delete_message/<int:message_id>', methods=['POST'])
@login_required
def delete_message(message_id):
    message = ub.session.query(ub.Message).get(message_id)
    
    if message and message.sender_id == current_user.id:
        ub.session.delete(message)
        ub.session.commit()
        flash('Mensaje eliminado.', 'success')
    else:
        flash('No tienes permiso para eliminar este mensaje.', 'danger')

    return redirect(url_for('chat', user_id=message.receiver_id if message.sender_id == current_user.id else message.sender_id))


@app.route('/notifications', methods=['GET'])
@login_required
def notifications():
    notifications = ub.session.query(ub.Notification).filter(ub.Notification.user_id == current_user.id).order_by(ub.Notification.timestamp.desc()).all()
    
    processed_notifications = []
    for notification in notifications:
        if 'ha comenzado a seguirte' in notification.message.lower():
            # Obtener el nombre del seguidor
            follower_name = notification.message.split(' ')[0]
            follower = ub.session.query(ub.User).filter(ub.User.name == follower_name).first()
            notification.follower_name = follower_name
            notification.follower_id = follower.id if follower else None

        elif 'mensaje de' in notification.message.lower():
            # Obtener el nombre del remitente del mensaje
            parts = notification.message.split('mensaje de ')[1]
            sender_name = parts.split(' ')[0]
            sender = ub.session.query(ub.User).filter(ub.User.name == sender_name).first()
            notification.sender_id = sender.id if sender else None

        processed_notifications.append(notification)

    return render_title_template('notifications.html', notifications=processed_notifications, title="Notificaciones", page='notifications')

@app.route('/notifications/delete_all', methods=['POST'])
@login_required
def delete_all_notifications():
    notifications = ub.session.query(ub.Notification).filter(ub.Notification.user_id == current_user.id).all()
    
    if not notifications:
        flash('No hay notificaciones para eliminar.', 'danger')
    else:
        ub.session.query(ub.Notification).filter(ub.Notification.user_id == current_user.id).delete()
        ub.session.commit()
        flash('Todas las notificaciones han sido eliminadas.', 'success')

    return redirect(url_for('notifications'))



@app.route('/notifications/delete/<int:notification_id>', methods=['POST'])
@login_required
def delete_notification(notification_id):
    notification = ub.session.query(ub.Notification).filter(ub.Notification.id == notification_id, ub.Notification.user_id == current_user.id).first()
    if notification:
        ub.session.delete(notification)
        ub.session.commit()
        flash('Notificación eliminada.', 'success')
    else:
        flash('No se pudo eliminar la notificación.', 'danger')
    return redirect(url_for('notifications'))

@app.route('/follow/<username>', methods=['GET'])
@login_required
def follow(username):
    user = ub.session.query(ub.User).filter(ub.User.name == username).first()
    if user is None:
        flash('Usuario {} no encontrado.'.format(username))
        return redirect(url_for('web.index'))
    if user == current_user:
        flash('¡No puedes seguirte a ti mismo!')
        return redirect(url_for('user_profile', username=username))
    
    current_user.follow(user)
    ub.session.commit()
    
    # Crear notificación para el usuario seguido
    notification = ub.Notification(
        user_id=user.id,
        message=f"{current_user.name} ha comenzado a seguirte.",
    )
    ub.session.add(notification)
    ub.session.commit()
    
    flash('¡Ahora sigues a {}!'.format(username))
    
    next_page = request.args.get('next')
    if next_page:
        return redirect(next_page)
    else:
        return redirect(url_for('user_profile', username=username))

@app.route('/unfollow/<username>', methods=['GET'])
@login_required
def unfollow(username):
    user = ub.session.query(ub.User).filter(ub.User.name == username).first()
    if user is None:
        flash('Usuario {} no encontrado.'.format(username))
        return redirect(url_for('web.index'))
    if user == current_user:
        flash('¡No puedes seguirte a ti mismo!')
        return redirect(url_for('user_profile', username=username))
    
    current_user.unfollow(user)
    ub.session.commit()
    flash('Has dejado de seguir a {}.'.format(username))
    
    next_page = request.args.get('next')
    if next_page:
        return redirect(next_page)
    else:
        return redirect(url_for('user_profile', username=username))

@app.route('/following/<username>')
@login_required
def following(username):
    user = ub.session.query(ub.User).filter_by(name=username).first()
    if user is None:
        abort(404)
    following_ids = [assoc.followed_id for assoc in user.following_associations]
    users = ub.session.query(ub.User).filter(ub.User.id.in_(following_ids)).all()
    return render_title_template('following.html', users=users, title=f"Seguidos por {user.name}", page='following', current_user_name=username)

@app.route('/followers/<username>')
@login_required
def followers(username):
    user = ub.session.query(ub.User).filter_by(name=username).first()
    if user is None:
        abort(404)
    follower_ids = [assoc.follower_id for assoc in user.follower_associations]
    users = ub.session.query(ub.User).filter(ub.User.id.in_(follower_ids)).all()
    return render_title_template('followers.html', users=users, title=f"Seguidores de {user.name}", page='followers', current_user_name=username)

@app.route('/searchFollow', methods=['GET'])
@login_required
def search():
    query = request.args.get('q', '')
    if query:
        users = ub.session.query(ub.User).filter(ub.User.name.like(f'{query}%')).all()
    else:
        users = []
    return render_title_template('searchFollow.html', users=users, query=query, title="Search profiles", page='search Profiles')


@app.route('/user/<username>')
@login_required
def user_profile(username):
    user = ub.session.query(ub.User).filter(ub.User.name == username).first()
    if user is None:
        abort(404)
    downloads = ub.session.query(ub.Downloads).filter(ub.Downloads.user_id == user.id).all()
    shelves = ub.session.query(ub.Shelf).filter(ub.Shelf.user_id == user.id).all()
    threads = ub.session.query(ub.Thread).filter(ub.Thread.user_id == user.id).all()
    
    books_dict = {}
    for download in downloads:
        book_id = download.book_id
        book = calibre_db.get_book(book_id)
        if book:
            cover_url = url_for('web.get_cover', book_id=book_id, resolution='md')
            books_dict[book_id] = {
                'book': book,
                'cover_url': cover_url
            }

    return render_title_template(
        'user_profile.html',
        user=user, downloads=downloads, books=books_dict, shelves=shelves, threads=threads, current_user=current_user,
        title="Profile",
        page='profile'
    )
################################ RED SOCIAL ################################


    
@web.route('/send/<int:book_id>/<book_format>/<int:convert>', methods=["POST"])
@login_required_if_no_ano
@download_required
def send_to_ereader(book_id, book_format, convert):
    if not config.get_mail_server_configured():
        response = [{'type': "danger", 'message': _("Please configure the SMTP mail settings first...")}]
        return Response(json.dumps(response), mimetype='application/json')
    elif current_user.kindle_mail:
        result = send_mail(book_id, book_format, convert, current_user.kindle_mail, config.get_book_path(),
                           current_user.name)
        if result is None:
            ub.update_download(book_id, int(current_user.id))
            response = [{'type': "success", 'message': _("Success! Book queued for sending to %(eReadermail)s",
                                                       eReadermail=current_user.kindle_mail)}]
        else:
            response = [{'type': "danger", 'message': _("Oops! There was an error sending book: %(res)s", res=result)}]
    else:
        response = [{'type': "danger", 'message': _("Oops! Please update your profile with a valid eReader Email.")}]
    return Response(json.dumps(response), mimetype='application/json')


# ################################### Login Logout ##################################################################

@web.route('/register', methods=['POST'])
@limiter.limit("40/day", key_func=get_remote_address)
@limiter.limit("3/minute", key_func=get_remote_address)
def register_post():
    if not config.config_public_reg:
        abort(404)
    to_save = request.form.to_dict()
    try:
        limiter.check()
    except RateLimitExceeded:
        flash(_(u"Please wait one minute to register next user"), category="error")
        return render_title_template('register.html', config=config, title=_("Register"), page="register")
    except (ConnectionError, Exception) as e:
        log.error("Connection error to limiter backend: %s", e)
        flash(_("Connection error to limiter backend, please contact your administrator"), category="error")
        return render_title_template('register.html', config=config, title=_("Register"), page="register")
    if current_user is not None and current_user.is_authenticated:
        return redirect(url_for('web.index'))
    if not config.get_mail_server_configured():
        flash(_("Oops! Email server is not configured, please contact your administrator."), category="error")
        return render_title_template('register.html', title=_("Register"), page="register")
    nickname = to_save.get("email", "").strip() if config.config_register_email else to_save.get('name')
    if not nickname or not to_save.get("email"):
        flash(_("Oops! Please complete all fields."), category="error")
        return render_title_template('register.html', title=_("Register"), page="register")
    try:
        nickname = check_username(nickname)
        email = check_email(to_save.get("email", ""))
    except Exception as ex:
        flash(str(ex), category="error")
        return render_title_template('register.html', title=_("Register"), page="register")

    content = ub.User()
    if check_valid_domain(email):
        content.name = nickname
        content.email = email
        password = generate_random_password(config.config_password_min_length)
        content.password = generate_password_hash(password)
        content.role = config.config_default_role
        content.locale = config.config_default_locale
        content.sidebar_view = config.config_default_show
        try:
            ub.session.add(content)
            ub.session.commit()
            if feature_support['oauth']:
                register_user_with_oauth(content)
            send_registration_mail(to_save.get("email", "").strip(), nickname, password)
        except Exception:
            ub.session.rollback()
            flash(_("Oops! An unknown error occurred. Please try again later."), category="error")
            return render_title_template('register.html', title=_("Register"), page="register")
    else:
        flash(_("Oops! Your Email is not allowed."), category="error")
        log.warning('Registering failed for user "{}" Email: {}'.format(nickname, to_save.get("email","")))
        return render_title_template('register.html', title=_("Register"), page="register")
    flash(_("Success! Confirmation Email has been sent."), category="success")
    return redirect(url_for('web.login'))


@web.route('/register', methods=['GET'])
def register():
    if not config.config_public_reg:
        abort(404)
    if current_user is not None and current_user.is_authenticated:
        return redirect(url_for('web.index'))
    if not config.get_mail_server_configured():
        flash(_("Oops! Email server is not configured, please contact your administrator."), category="error")
        return render_title_template('register.html', title=_("Register"), page="register")
    if feature_support['oauth']:
        register_user_with_oauth()
    return render_title_template('register.html', config=config, title=_("Register"), page="register")


def handle_login_user(user, remember, message, category):
    login_user(user, remember=remember)
    ub.store_user_session()
    flash(message, category=category)
    [limiter.limiter.storage.clear(k.key) for k in limiter.current_limits]
    return redirect(get_redirect_location(request.form.get('next', None), "web.index"))


def render_login(username="", password=""):
    next_url = request.args.get('next', default=url_for("web.index"), type=str)
    if url_for("web.logout") == next_url:
        next_url = url_for("web.index")
    return render_title_template('login.html',
                                 title=_("Login"),
                                 next_url=next_url,
                                 config=config,
                                 username=username,
                                 password=password,
                                 oauth_check=oauth_check,
                                 mail=config.get_mail_server_configured(), page="login")


@web.route('/login', methods=['GET'])
def login():
    if current_user is not None and current_user.is_authenticated:
        return redirect(url_for('web.index'))
    if config.config_login_type == constants.LOGIN_LDAP and not services.ldap:
        log.error(u"Cannot activate LDAP authentication")
        flash(_(u"Cannot activate LDAP authentication"), category="error")
    return render_login()


@web.route('/login', methods=['POST'])
@limiter.limit("40/day", key_func=lambda: request.form.get('username', "").strip().lower())
@limiter.limit("3/minute", key_func=lambda: request.form.get('username', "").strip().lower())
def login_post():
    form = request.form.to_dict()
    username = form.get('username', "").strip().lower().replace("\n","").replace("\r","")
    try:
        limiter.check()
    except RateLimitExceeded:
        flash(_("Please wait one minute before next login"), category="error")
        return render_login(username, form.get("password", ""))
    except (ConnectionError, Exception) as e:
        log.error("Connection error to limiter backend: %s", e)
        flash(_("Connection error to limiter backend, please contact your administrator"), category="error")
        return render_login(username, form.get("password", ""))
    if current_user is not None and current_user.is_authenticated:
        return redirect(url_for('web.index'))
    if config.config_login_type == constants.LOGIN_LDAP and not services.ldap:
        log.error(u"Cannot activate LDAP authentication")
        flash(_(u"Cannot activate LDAP authentication"), category="error")
    user = ub.session.query(ub.User).filter(func.lower(ub.User.name) == username).first()
    remember_me = bool(form.get('remember_me'))
    if config.config_login_type == constants.LOGIN_LDAP and services.ldap and user and form['password'] != "":
        login_result, error = services.ldap.bind_user(username, form['password'])
        if login_result:
            log.debug(u"You are now logged in as: '{}'".format(user.name))
            return handle_login_user(user,
                                     remember_me,
                                     _(u"you are now logged in as: '%(nickname)s'", nickname=user.name),
                                     "success")
        elif login_result is None and user and check_password_hash(str(user.password), form['password']) \
                and user.name != "Guest":
            log.info("Local Fallback Login as: '{}'".format(user.name))
            return handle_login_user(user,
                                     remember_me,
                                     _(u"Fallback Login as: '%(nickname)s', "
                                       u"LDAP Server not reachable, or user not known", nickname=user.name),
                                     "warning")
        elif login_result is None:
            log.info(error)
            flash(_(u"Could not login: %(message)s", message=error), category="error")
        else:
            ip_address = request.headers.get('X-Forwarded-For', request.remote_addr)
            log.warning('LDAP Login failed for user "%s" IP-address: %s', username, ip_address)
            flash(_(u"Wrong Username or Password"), category="error")
    else:
        ip_address = request.headers.get('X-Forwarded-For', request.remote_addr)
        if form.get('forgot', "") == 'forgot':
            if user is not None and user.name != "Guest":
                ret, __ = reset_password(user.id)
                if ret == 1:
                    flash(_(u"New Password was sent to your email address"), category="info")
                    log.info('Password reset for user "%s" IP-address: %s', username, ip_address)
                else:
                    log.error(u"An unknown error occurred. Please try again later")
                    flash(_(u"An unknown error occurred. Please try again later."), category="error")
            else:
                flash(_(u"Please enter valid username to reset password"), category="error")
                log.warning('Username missing for password reset IP-address: %s', ip_address)
        else:
            if user and check_password_hash(str(user.password), form['password']) and user.name != "Guest":
                config.config_is_initial = False
                log.debug(u"You are now logged in as: '{}'".format(user.name))
                return handle_login_user(user,
                                         remember_me,
                                         _(u"You are now logged in as: '%(nickname)s'", nickname=user.name),
                                         "success")
            else:
                log.warning('Login failed for user "{}" IP-address: {}'.format(username, ip_address))
                flash(_(u"Wrong Username or Password"), category="error")
    return render_login(username, form.get("password", ""))


@web.route('/logout')
@login_required
def logout():
    if current_user is not None and current_user.is_authenticated:
        ub.delete_user_session(current_user.id, flask_session.get('_id', ""))
        logout_user()
        if feature_support['oauth'] and (config.config_login_type == 2 or config.config_login_type == 3):
            logout_oauth_user()
    log.debug("User logged out")
    if config.config_anonbrowse:
        location = get_redirect_location(request.args.get('next', None), "web.login")
    else:
        location = None
    if location:
        return redirect(location)
    else:
        return redirect(url_for('web.login'))


# ################################### Users own configuration #########################################################
def change_profile(kobo_support, local_oauth_check, oauth_status, translations, languages):
    to_save = request.form.to_dict()
    current_user.random_books = 0
    try:
        if current_user.role_passwd() or current_user.role_admin():
            if to_save.get("password", "") != "":
                current_user.password = generate_password_hash(valid_password(to_save.get("password")))
        if to_save.get("kindle_mail", current_user.kindle_mail) != current_user.kindle_mail:
            current_user.kindle_mail = valid_email(to_save.get("kindle_mail"))
        new_email = valid_email(to_save.get("email", current_user.email))
        if not new_email:
            raise Exception(_("Email can't be empty and has to be a valid Email"))
        if new_email != current_user.email:
            current_user.email = check_email(new_email)
        if current_user.role_admin():
            if to_save.get("name", current_user.name) != current_user.name:
                # Query username, if not existing, change
                current_user.name = check_username(to_save.get("name"))
        current_user.random_books = 1 if to_save.get("show_random") == "on" else 0
        current_user.default_language = to_save.get("default_language", "all")
        current_user.locale = to_save.get("locale", "en")
        old_state = current_user.kobo_only_shelves_sync
        # 1 -> 0: nothing has to be done
        # 0 -> 1: all synced books have to be added to archived books, + currently synced shelfs which
        # don't have to be synced have to be removed (added to Shelf archive)
        current_user.kobo_only_shelves_sync = int(to_save.get("kobo_only_shelves_sync") == "on") or 0
        if old_state == 0 and current_user.kobo_only_shelves_sync == 1:
            kobo_sync_status.update_on_sync_shelfs(current_user.id)

    except Exception as ex:
        flash(str(ex), category="error")
        return render_title_template("user_edit.html",
                                     content=current_user,
                                     config=config,
                                     translations=translations,
                                     profile=1,
                                     languages=languages,
                                     title=_("%(name)s's Profile", name=current_user.name),
                                     page="me",
                                     kobo_support=kobo_support,
                                     registered_oauth=local_oauth_check,
                                     oauth_status=oauth_status)

    val = 0
    for key, __ in to_save.items():
        if key.startswith('show'):
            val += int(key[5:])
    current_user.sidebar_view = val
    if to_save.get("Show_detail_random"):
        current_user.sidebar_view += constants.DETAIL_RANDOM

    try:
        ub.session.commit()
        flash(_("Success! Profile Updated"), category="success")
        log.debug("Profile updated")
    except IntegrityError:
        ub.session.rollback()
        flash(_("Oops! An account already exists for this Email."), category="error")
        log.debug("Found an existing account for this Email")
    except OperationalError as e:
        ub.session.rollback()
        log.error("Database error: %s", e)
        flash(_("Oops! Database Error: %(error)s.", error=e), category="error")


@web.route("/me", methods=["GET", "POST"])
@login_required
def profile():
    languages = calibre_db.speaking_language()
    translations = get_available_locale()
    kobo_support = feature_support['kobo'] and config.config_kobo_sync
    if feature_support['oauth'] and config.config_login_type == 2:
        oauth_status = get_oauth_status()
        local_oauth_check = oauth_check
    else:
        oauth_status = None
        local_oauth_check = {}

    if request.method == "POST":
        change_profile(kobo_support, local_oauth_check, oauth_status, translations, languages)
    return render_title_template("user_edit.html",
                                 translations=translations,
                                 profile=1,
                                 languages=languages,
                                 content=current_user,
                                 config=config,
                                 kobo_support=kobo_support,
                                 title=_("%(name)s's Profile", name=current_user.name),
                                 page="me",
                                 registered_oauth=local_oauth_check,
                                 oauth_status=oauth_status)


# ###################################Show single book ##################################################################


@web.route("/read/<int:book_id>/<book_format>")
@login_required_if_no_ano
@viewer_required
def read_book(book_id, book_format):
    book = calibre_db.get_filtered_book(book_id)

    if not book:
        flash(_("Oops! Selected book is unavailable. File does not exist or is not accessible"),
              category="error")
        log.debug("Selected book is unavailable. File does not exist or is not accessible")
        return redirect(url_for("web.index"))

    book.ordered_authors = calibre_db.order_authors([book], False)

    # check if book has a bookmark
    bookmark = None
    if current_user.is_authenticated:
        bookmark = ub.session.query(ub.Bookmark).filter(and_(ub.Bookmark.user_id == int(current_user.id),
                                                             ub.Bookmark.book_id == book_id,
                                                             ub.Bookmark.format == book_format.upper())).first()
    if book_format.lower() == "epub":
        log.debug("Start epub reader for %d", book_id)
        return render_title_template('read.html', bookid=book_id, title=book.title, bookmark=bookmark)
    elif book_format.lower() == "pdf":
        log.debug("Start pdf reader for %d", book_id)
        return render_title_template('readpdf.html', pdffile=book_id, title=book.title)
    elif book_format.lower() == "txt":
        log.debug("Start txt reader for %d", book_id)
        return render_title_template('readtxt.html', txtfile=book_id, title=book.title)
    elif book_format.lower() in ["djvu", "djv"]:
        log.debug("Start djvu reader for %d", book_id)
        return render_title_template('readdjvu.html', djvufile=book_id, title=book.title,
                                     extension=book_format.lower())
    else:
        for fileExt in constants.EXTENSIONS_AUDIO:
            if book_format.lower() == fileExt:
                entries = calibre_db.get_filtered_book(book_id)
                log.debug("Start mp3 listening for %d", book_id)
                return render_title_template('listenmp3.html', mp3file=book_id, audioformat=book_format.lower(),
                                             entry=entries, bookmark=bookmark)
        for fileExt in ["cbr", "cbt", "cbz"]:
            if book_format.lower() == fileExt:
                all_name = str(book_id)
                title = book.title
                if len(book.series):
                    title = title + " - " + book.series[0].name
                    if book.series_index:
                        title = title + " #" + '{0:.2f}'.format(book.series_index).rstrip('0').rstrip('.')
                log.debug("Start comic reader for %d", book_id)
                return render_title_template('readcbr.html', comicfile=all_name, title=title,
                                             extension=fileExt, bookmark=bookmark)
        log.debug("Selected book is unavailable. File does not exist or is not accessible")
        flash(_("Oops! Selected book is unavailable. File does not exist or is not accessible"),
              category="error")
        return redirect(url_for("web.index"))


@web.route("/book/<int:book_id>")
@login_required_if_no_ano
def show_book(book_id):
    entries = calibre_db.get_book_read_archived(book_id, config.config_read_column, allow_show_archived=True)
    if entries:
        read_book = entries[1]
        archived_book = entries[2]
        entry = entries[0]
        entry.read_status = read_book == ub.ReadBook.STATUS_FINISHED
        entry.is_archived = archived_book
        for lang_index in range(0, len(entry.languages)):
            entry.languages[lang_index].language_name = isoLanguages.get_language_name(get_locale(), entry.languages[
                lang_index].lang_code)
        cc = calibre_db.get_cc_columns(config, filter_config_custom_read=True)
        book_in_shelves = []
        shelves = ub.session.query(ub.BookShelf).filter(ub.BookShelf.book_id == book_id).all()
        for sh in shelves:
            book_in_shelves.append(sh.shelf)

        entry.tags = sort(entry.tags, key=lambda tag: tag.name)

        entry.ordered_authors = calibre_db.order_authors([entry])

        entry.email_share_list = check_send_to_ereader(entry)
        entry.reader_list = check_read_formats(entry)

        entry.audio_entries = []
        for media_format in entry.data:
            if media_format.format.lower() in constants.EXTENSIONS_AUDIO:
                entry.audio_entries.append(media_format.format.lower())

        return render_title_template('detail.html',
                                     entry=entry,
                                     cc=cc,
                                     is_xhr=request.headers.get('X-Requested-With') == 'XMLHttpRequest',
                                     title=entry.title,
                                     books_shelfs=book_in_shelves,
                                     page="book")
    else:
        log.debug("Selected book is unavailable. File does not exist or is not accessible")
        flash(_("Oops! Selected book is unavailable. File does not exist or is not accessible"),
              category="error")
        return redirect(url_for("web.index"))
