from datetime import datetime
import json
import requests
from requests_oauthlib import OAuth1


class ResourceUnavailable(Exception):
    """Exception representing a failed request to a resource"""

    def __init__(self, msg, http_response):
        Exception.__init__(self)
        self._msg = msg
        self._status = http_response.status_code

    def __str__(self):
        return "%s (HTTP status: %s)" % (
        self._msg, self._status)


class Unauthorized(ResourceUnavailable):
    pass


class TokenError(Exception):
    pass


class TrelloClient(object):
    """ Base class for Trello API access """

    def __init__(self, api_key, api_secret=None, token=None, token_secret=None):
        """
        Constructor

        :api_key: API key generated at https://trello.com/1/appKey/generate
        :api_secret: the secret component of api_key
        :token_key: OAuth token generated by the user in
                    trello.util.create_oauth_token
        :token_secret: the OAuth client secret for the given OAuth token
        """

        # client key and secret for oauth1 session
        if api_key or token:
            self.oauth = OAuth1(client_key=api_key, client_secret=api_secret,
                                resource_owner_key=token, resource_owner_secret=token_secret)
        else:
            self.oauth = None

        self.public_only = token is None
        self.api_key = api_key
        self.api_secret = api_secret
        self.resource_owner_key = token
        self.resource_owner_secret = token_secret

    def info_for_all_boards(self, actions):
        """
        Use this if you want to retrieve info for all your boards in one swoop
        """
        if self.public_only:
            return None
        else:
            json_obj = self.fetch_json(
                '/members/me/boards/all',
                query_params={'actions': actions})
            self.all_info = json_obj

    def logout(self):
        """Log out of Trello."""
        #TODO: This function.

        raise NotImplementedError()

    def list_boards(self):
        """
        Returns all boards for your Trello user

        :return: a list of Python objects representing the Trello boards.
        Each board has the following noteworthy attributes:
            - id: the board's identifier
            - name: Name of the board
            - desc: Description of the board (optional - may be missing from the
                    returned JSON)
            - closed: Boolean representing whether this board is closed or not
            - url: URL to the board
        """
        json_obj = self.fetch_json('/members/me/boards')
        return [Board.from_json(self, obj) for obj in json_obj]

    def get_board(self, board_id):
        obj = self.fetch_json('/boards/' + board_id)
        return Board.from_json(self, obj)

    def add_board(self, board_name):
        obj = self.fetch_json('/boards', http_method='POST',
                              post_args={'name': board_name})
        return Board.from_json(self, obj)

    def get_member(self, member_id):
        return Member(self, member_id).fetch()

    def fetch_json(
            self,
            uri_path,
            http_method='GET',
            headers=None,
            query_params=None,
            post_args=None):
        """ Fetch some JSON from Trello """

        # explicit values here to avoid mutable default values
        if headers is None:
            headers = {}
        if query_params is None:
            query_params = {}
        if post_args is None:
            post_args = {}

        # set content type and accept headers to handle JSON
        if http_method in ("POST", "PUT", "DELETE"):
            headers['Content-Type'] = 'application/json; charset=utf-8'
        headers['Accept'] = 'application/json'

        # construct the full URL without query parameters
        if uri_path[0] == '/':
            uri_path = uri_path[1:]
        url = 'https://api.trello.com/1/%s' % uri_path

        # perform the HTTP requests, if possible uses OAuth authentication
        response = requests.request(http_method, url, params=query_params, verify=True,
                                    headers=headers, data=json.dumps(post_args), auth=self.oauth)

        if response.status_code == 401:
            print "Unauthorized"
            raise Unauthorized("%s at %s" % (response.text, url), response)
        if response.status_code != 200:
            print "resource unavailable"
            raise ResourceUnavailable("%s at %s" % (response.text, url), response)

        return response.json()

    def list_hooks(self, token=None):
        """
        Returns a list of all hooks associated with a specific token. If you don't pass in a token,
        it tries to use the token associated with the TrelloClient object (if it exists)
        """
        token = token or self.resource_owner_key

        if token is None:
            raise TokenError("You need to pass an auth token in to list hooks.")
        else:
            url = "/tokens/%s/webhooks" % token
            return self._existing_hook_objs(self.fetch_json(url), token)

    def _existing_hook_objs(self, hooks, token):
        """
        Given a list of hook dicts passed from list_hooks, creates
        the hook objects
        """
        all_hooks = []
        for hook in hooks:
            new_hook = WebHook(self, token, hook['id'], hook['description'],
                               hook['idModel'],
                               hook['callbackURL'], hook['active'])
            all_hooks.append(new_hook)
        return all_hooks

    def create_hook(self, callback_url, id_model, desc=None, token=None):
        """
        Creates a new webhook. Returns the WebHook object created.

        There seems to be some sort of bug that makes you unable to create a
        hook using httplib2, so I'm using urllib2 for that instead.
        """
        token = token or self.resource_owner_key

        if token is None:
            raise TokenError("You need to pass an auth token in to create a hook.")

        url = "https://trello.com/1/tokens/%s/webhooks/" % token
        data = {'callbackURL': callback_url, 'idModel': id_model,
                'description': desc}

        print "creating a webhook in trello.py: url=%s, data=%s." % (url, str(data))

        response = requests.post(url, data=data, auth=self.oauth)
        #response = requests.post(url, data=data)
        print "response text: ", response.text
        print "response status: ", response.status_code

        if response.status_code == 200:
            hook_id = response.json()['id']
            print "hook_id: ", hook_id
            return WebHook(self, token, hook_id, desc, id_model, callback_url, True)
        else:
            return False


class Board(object):
    """
    Class representing a Trello board. Board attributes are stored as normal
    Python attributes; access to all sub-objects, however, is always
    an API call (Lists, Cards).
    """

    def __init__(self, client, board_id, name=''):
        """
        :trello: Reference to a Trello object
        :board_id: ID for the board
        """
        self.client = client
        self.id = board_id
        self.name = name

    @classmethod
    def from_json(cls, trello_client, json_obj):
        """
        Deserialize the board json object to a Board object

        :trello_client: the trello client
        :json_obj: the board json object
        """
        board = Board(trello_client, json_obj['id'], name=json_obj['name'].encode('utf-8'))
        board.description = json_obj.get('desc', '').encode('utf-8')
        board.closed = json_obj['closed']
        board.url = json_obj['url']
        return board

    def __repr__(self):
        return '<Board %s>' % self.name

    def fetch(self):
        """Fetch all attributes for this board"""
        json_obj = self.client.fetch_json('/boards/' + self.id)
        self.name = json_obj['name']
        self.description = json_obj.get('desc', '')
        self.closed = json_obj['closed']
        self.url = json_obj['url']

    def save(self):
        pass

    def close(self):
        self.client.fetch_json(
            '/boards/' + self.id + '/closed',
            http_method='PUT',
            post_args={'value': 'true', }, )
        self.closed = True

    def get_list(self, list_id):
        obj = self.client.fetch_json('/lists/' + list_id)
        return List.from_json(board=self, json_obj=obj)

    def all_lists(self):
        """Returns all lists on this board"""
        return self.get_lists('all')

    def open_lists(self):
        """Returns all open lists on this board"""
        return self.get_lists('open')

    def closed_lists(self):
        """Returns all closed lists on this board"""
        return self.get_lists('closed')

    def get_lists(self, list_filter):
        # error checking
        json_obj = self.client.fetch_json(
            '/boards/' + self.id + '/lists',
            query_params={'cards': 'none', 'filter': list_filter})
        return [List.from_json(board=self, json_obj=obj) for obj in json_obj]

    def add_list(self, name):
        """Add a list to this board

        :name: name for the list
        :return: the list
        """
        obj = self.client.fetch_json(
            '/lists',
            http_method='POST',
            post_args={'name': name, 'idBoard': self.id}, )
        return List.from_json(board=self, json_obj=obj)

    def all_cards(self):
        """Returns all cards on this board"""
        filters = {
            'filter': 'all',
            'fields': 'all'
        }
        return self.get_cards(filters)

    def open_cards(self):
        """Returns all open cards on this board"""
        filters = {
            'filter': 'open',
            'fields': 'all'
        }
        return self.get_cards(filters)

    def closed_cards(self):
        """Returns all closed cards on this board"""
        filters = {
            'filter': 'closed',
            'fields': 'all'
        }
        return self.get_cards(filters)

    def get_cards(self, filters=None):
        """
        :card_filter: filters on card status ('open', 'closed', 'all')
        :query_params: dict containing query parameters. Eg. {'fields': 'all'}

        More info on card queries:
        https://trello.com/docs/api/board/index.html#get-1-boards-board-id-cards
        """
        json_obj = self.client.fetch_json(
            '/boards/' + self.id + '/cards',
            query_params=filters
        )

        cards = list()
        for card_json in json_obj:
            card = Card(self, card_json['id'],
                        name=card_json['name'])

            for card_key, card_val in card_json.items():
                if card_key in ['id', 'name']:
                    continue

                setattr(card, card_key, card_val)

            cards.append(card)

        return cards

    def all_members(self):
        """Returns all members on this board"""
        filters = {
            'filter': 'all',
            'fields': 'all'
        }
        return self.get_members(filters)

    def normal_members(self):
        """Returns all normal members on this board"""
        filters = {
            'filter': 'normal',
            'fields': 'all'
        }
        return self.get_members(filters)

    def admin_members(self):
        """Returns all admin members on this board"""
        filters = {
            'filter': 'admins',
            'fields': 'all'
        }
        return self.get_members(filters)

    def owner_members(self):
        """Returns all owner members on this board"""
        filters = {
            'filter': 'owners',
            'fields': 'all'
        }
        return self.get_members(filters)

    def get_members(self, filters=None):
        json_obj = self.client.fetch_json(
            '/boards/' + self.id + '/members',
            query_params=filters)
        members = list()
        for obj in json_obj:
            m = Member(self.client, obj['id'])
            m.status = obj['status'].encode('utf-8')
            m.id = obj.get('id', '')
            m.bio = obj.get('bio', '')
            m.url = obj.get('url', '')
            m.username = obj['username'].encode('utf-8')
            m.full_name = obj['fullName'].encode('utf-8')
            m.initials = obj['initials'].encode('utf-8')
            members.append(m)

        return members

    def fetch_actions(self, action_filter):
        json_obj = self.client.fetch_json(
            '/boards/' + self.id + '/actions',
            query_params={'filter': action_filter})
        self.actions = json_obj


class List(object):
    """
    Class representing a Trello list. List attributes are stored on the object,
    but access to sub-objects (Cards) require an API call
    """

    def __init__(self, board, list_id, name=''):
        """Constructor

        :board: reference to the parent board
        :list_id: ID for this list
        """
        self.board = board
        self.client = board.client
        self.id = list_id
        self.name = name

    @classmethod
    def from_json(cls, board, json_obj):
        """
        Deserialize the list json object to a List object

        :board: the board object that the list belongs to
        :json_obj: the json list object
        """
        list = List(board, json_obj['id'], name=json_obj['name'].encode('utf-8'))
        list.closed = json_obj['closed']
        return list

    def __repr__(self):
        return '<List %s>' % self.name

    def fetch(self):
        """Fetch all attributes for this list"""
        json_obj = self.client.fetch_json('/lists/' + self.id)
        self.name = json_obj['name']
        self.closed = json_obj['closed']

    def list_cards(self):
        """Lists all cards in this list"""
        json_obj = self.client.fetch_json('/lists/' + self.id + '/cards')
        return [Card.from_json(self, c) for c in json_obj]

    def add_card(self, name, desc=None, pos='top'):
        """Add a card to this list

        :name: name for the card
        :return: the card
        """
        # json_obj = self.client.fetch_json(
        #     '/lists/' + self.id + '/cards',
        #     http_method='POST',
        #     post_args={'name': name, 'idList': self.id, 'desc': desc, 'pos': pos}, )
        # return Card.from_json(self, json_obj)
        json_obj = self.client.fetch_json(
            '/cards/',
            http_method='POST',
            post_args={'name': name, 'idList': self.id, 'desc': desc, 'pos': pos, 'urlSource': 'null'}, )
        return Card.from_json(self, json_obj)

    def fetch_actions(self, action_filter):
        """
        Fetch actions for this list can give more argv to action_filter,
        split for ',' json_obj is list
        """
        json_obj = self.client.fetch_json(
            '/lists/' + self.id + '/actions',
            query_params={'filter': action_filter})
        self.actions = json_obj

    def _set_remote_attribute(self, attribute, value):
        self.client.fetch_json(
            '/lists/' + self.id + '/' + attribute,
            http_method='PUT',
            post_args={'value': value, }, )

    def close(self):
        self.client.fetch_json(
            '/lists/' + self.id + '/closed',
            http_method='PUT',
            post_args={'value': 'true', }, )
        self.closed = True


class Card(object):
    """
    Class representing a Trello card. Card attributes are stored on
    the object
    """

    @property
    def member_id(self):
        return self.idMembers

    @property
    def short_id(self):
        return self.idShort

    @property
    def list_id(self):
        return self.idList

    @property
    def board_id(self):
        return self.idBoard

    @property
    def description(self):
        return self.desc

    @description.setter
    def description(self, value):
        self.desc = value

    def __init__(self, trello_list, card_id, name=''):
        """
        :trello_list: reference to the parent list
        :card_id: ID for this card
        """
        self.trello_list = trello_list
        self.client = trello_list.client
        self.id = card_id
        self.name = name

    @classmethod
    def from_json(cls, trello_list, json_obj):
        """
        Deserialize the card json object to a Card object

        :trello_list: the list object that the card belongs to
        :json_obj: json object
        """
        if 'id' not in json_obj:
            raise Exception("key 'id' is not in json_obj")
        card = cls(trello_list,
                   json_obj['id'],
                   name=json_obj['name'].encode('utf-8'))
        card.desc = json_obj.get('desc', '')
        card.closed = json_obj['closed']
        card.url = json_obj['url']
        card.member_ids = json_obj['idMembers']
        return card

    def __repr__(self):
        return '<Card %s>' % self.name

    def fetch(self):
        """Fetch all attributes for this card"""
        json_obj = self.client.fetch_json(
            '/cards/' + self.id,
            query_params={'badges': False})
        self.name = json_obj['name'].encode('utf-8')
        self.desc = json_obj.get('desc', '')
        self.closed = json_obj['closed']
        self.url = json_obj['url']
        self.idMembers = json_obj['idMembers']
        self.idShort = json_obj['idShort']
        self.idList = json_obj['idList']
        self.idBoard = json_obj['idBoard']
        self.labels = json_obj['labels']
        self.badges = json_obj['badges']
        # For consistency, due date is in YYYY-MM-DD format
        #self.due = json_obj.get('due', '')[:10]
        self.checked = json_obj['checkItemStates']

        self.checklists = []
        if self.badges['checkItems'] > 0:
            json_obj = self.client.fetch_json(
                '/cards/' + self.id + '/checklists', )
            for cl in json_obj:
                self.checklists.append(Checklist(self.client, self.checked, cl,
                                                 trello_card=self.id))

        self.comments = []
        if self.badges['comments'] > 0:
            self.comments = self.client.fetch_json(
                '/cards/' + self.id + '/actions',
                query_params={'filter': 'commentCard'})

    def fetch_actions(self, action_filter='createCard'):
        """
        Fetch actions for this card can give more argv to action_filter,
        split for ',' json_obj is list
        """
        json_obj = self.client.fetch_json(
            '/cards/' + self.id + '/actions',
            query_params={'filter': action_filter})
        self.actions = json_obj

    @property
    def create_date(self):
        self.fetch_actions()
        date_str = self.actions[0]['date'][:-5]
        return datetime.strptime(date_str, '%Y-%m-%dT%H:%M:%S')

    def set_description(self, description):
        self._set_remote_attribute('desc', description)
        self.desc = description

    def set_due(self, due):
        """Set the due time for the card

        :title: due a datetime object
        """
        datestr = due.strftime('%Y-%m-%d')
        self._set_remote_attribute('due', datestr)
        self.due = datestr

    def set_closed(self, closed):
        self._set_remote_attribute('closed', closed)
        self.closed = closed

    def delete(self):
        # Delete this card permanently
        self.client.fetch_json(
            '/cards/' + self.id,
            http_method='DELETE', )

    def assign(self, member_id):
        self.client.fetch_json(
            '/cards/' + self.id + '/members',
            http_method='POST',
            post_args={'value': member_id, })

    def comment(self, comment_text):
        """Add a comment to a card."""
        self.client.fetch_json(
            '/cards/' + self.id + '/actions/comments',
            http_method='POST',
            post_args={'text': comment_text, })

    def change_list(self, list_id):
        self.client.fetch_json(
            '/cards/' + self.id + '/idList',
            http_method='PUT',
            post_args={'value': list_id, })

    def change_board(self, board_id, list_id=None):
        args = {'value': board_id, }
        if list_id is not None:
            args['idList'] = list_id
        self.client.fetch_json(
            '/cards/' + self.id + '/idBoard',
            http_method='PUT',
            post_args=args)

    def add_checklist(self, title, items, itemstates=None):

        """Add a checklist to this card

        :title: title of the checklist
        :items: a list of the item names
        :itemstates: a list of the state (True/False) of each item
        :return: the checklist
        """
        if itemstates is None:
            itemstates = []

        json_obj = self.client.fetch_json(
            '/cards/' + self.id + '/checklists',
            http_method='POST',
            post_args={'name': title}, )

        cl = Checklist(self.client, [], json_obj, trello_card=self.id)
        for i, name in enumerate(items):
            try:
                checked = itemstates[i]
            except IndexError:
                checked = False
            cl.add_checklist_item(name, checked)

        self.fetch()
        return cl

    def _set_remote_attribute(self, attribute, value):
        self.client.fetch_json(
            '/cards/' + self.id + '/' + attribute,
            http_method='PUT',
            post_args={'value': value, }, )

    def add_attachment(self, url, desc):
        self.client.fetch_json(
            '/cards/' + self.id + '/attachments',
            http_method='POST',
            post_args={'url': url, 'name': desc, }, )



class Member(object):
    """
    Class representing a Trello member.
    """

    def __init__(self, client, member_id):
        self.client = client
        self.id = member_id

    def __repr__(self):
        return '<Member %s>' % self.id

    def fetch(self):
        """Fetch all attributes for this card"""
        json_obj = self.client.fetch_json(
            '/members/' + self.id,
            query_params={'badges': False})
        self.status = json_obj['status']
        self.id = json_obj.get('id', '')
        self.bio = json_obj.get('bio', '')
        self.url = json_obj.get('url', '')
        self.username = json_obj['username']
        self.full_name = json_obj['fullName']
        self.initials = json_obj['initials']
        return self


class Checklist(object):
    """
    Class representing a Trello checklist.
    """

    def __init__(self, client, checked, obj, trello_card=None):
        self.client = client
        self.trello_card = trello_card
        self.id = obj['id']
        self.name = obj['name']
        self.items = obj['checkItems']
        for i in self.items:
            i['checked'] = False
            for cis in checked:
                if cis['idCheckItem'] == i['id'] and cis['state'] == 'complete':
                    i['checked'] = True

    def add_checklist_item(self, name, checked=False):
        """Add a checklist item to this checklist

        :name: name of the checklist item
        :checked: True if item state should be checked, False otherwise
        :return: the checklist item json object
        """
        json_obj = self.client.fetch_json(
            '/checklists/' + self.id + '/checkItems',
            http_method='POST',
            post_args={'name': name, 'checked': checked}, )
        json_obj['checked'] = checked
        self.items.append(json_obj)
        return json_obj

    def set_checklist_item(self, name, checked):
        """Set the state of an item on this checklist

        :name: name of the checklist item
        :checked: True if item state should be checked, False otherwise
        """

        # Locate the id of the checklist item
        try:
            [ix] = [i for i in range(len(self.items)) if
                    self.items[i]['name'] == name]
        except ValueError:
            return

        json_obj = self.client.fetch_json(
            '/cards/' + self.trello_card + \
            '/checklist/' + self.id + \
            '/checkItem/' + self.items[ix]['id'],
            http_method='PUT',
            post_args={'state': 'complete' if checked else 'incomplete'})

        json_obj['checked'] = checked
        self.items[ix] = json_obj
        return json_obj

    def rename_checklist_item(self, name, new_name):
        """Rename the item on this checklist

        :name: name of the checklist item
        :new_name: new name of item
        """

        # Locate the id of the checklist item
        try:
            [ix] = [i for i in range(len(self.items)) if self.items[i]['name'] == name]
        except ValueError:
            return
         
        json_obj = self.client.fetch_json(
                '/cards/'+self.trello_card+\
                '/checklist/'+self.id+\
                '/checkItem/'+self.items[ix]['id'],
                http_method = 'PUT',
                post_args = {'name' : new_name})
        
        self.items[ix] = json_obj 
        return json_obj

    def __repr__(self):
        return '<Checklist %s>' % self.id


class WebHook(object):
    """Class representing a Trello webhook."""

    def __init__(self, client, token, hook_id=None, desc=None, id_model=None,
                 callback_url=None, active=False):
        self.id = hook_id
        self.desc = desc
        self.id_model = id_model
        self.callback_url = callback_url
        self.active = active
        self.client = client
        self.token = token

    def delete(self):
        """Removes this webhook from Trello"""
        self.client.fetch_json(
            '/webhooks/%s' % self.id,
            http_method='DELETE')

# vim: tabstop=8 expandtab shiftwidth=4 softtabstop=4