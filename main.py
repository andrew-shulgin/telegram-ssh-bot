import configparser
import json
import logging
import time
from collections import OrderedDict
from datetime import datetime
from math import floor
from os.path import exists

import paramiko
import pytz as pytz
import telegram
import telegram.ext


class ConfigParserDict(configparser.ConfigParser):
    def sections_dict(self):
        return self._sections.copy()

    def options_dict(self, section):
        try:
            return self._sections[section].copy()
        except KeyError:
            raise configparser.NoSectionError(section)


logging.basicConfig(
    format='%(asctime)s [%(levelname)s] %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

config = ConfigParserDict()
config.optionxform = str
config.read('config.ini')

users = dict(config.options_dict('users'))
hosts = OrderedDict(
    (section[len('host:'):], dict(config.options_dict(section)))
    for section in config.sections()
    if section[:len('host:')] == 'host:'
)
history_path = config.get('bot', 'history', fallback=None)
tz = pytz.timezone(config.get('bot', 'tz', fallback='Etc/UTC'))


def update_history(new_item=None):
    if not history_path:
        return
    history = []
    if exists(history_path):
        with open(history_path, 'r') as fp:
            try:
                history = json.load(fp)
            except (TypeError, ValueError):
                pass
    if new_item:
        history.append(new_item)
    for item in history:
        if (time.time() - item['ts']) > 604800:  # 7 days
            history.remove(item)
    with open(history_path, 'w') as fp:
        json.dump(history, fp)


def get_history():
    if history_path:
        update_history()
        with open(history_path, 'r') as fp:
            try:
                return json.load(fp)
            except (TypeError, ValueError):
                pass
    return []


def error(*args):
    if len(args) == 3:
        logger.error(args[2])


def index(bot, user_id, chat_id, message_id=None):
    if str(user_id) not in users.keys():
        logger.warning('Unknown user: {}'.format(user_id))
        bot.sendMessage(
            text='User *{}* is not allowed here. Sorry.'.format(user_id),
            parse_mode='Markdown',
            chat_id=chat_id
        )
        return
    keyboard = [[]]
    row = keyboard[0]
    for host in hosts:
        row.append(telegram.InlineKeyboardButton(
            host,
            callback_data=json.dumps({'host': host})
        ))
        if len(keyboard[-1]) % 2 == 0:
            row = []
            keyboard.append(row)
    keyboard.append([telegram.InlineKeyboardButton(
        'History',
        callback_data=json.dumps('history')
    )])
    reply_markup = telegram.InlineKeyboardMarkup(keyboard)
    text = 'Please choose a host:'
    if message_id:
        bot.editMessageText(
            text=text,
            reply_markup=reply_markup,
            chat_id=chat_id,
            message_id=message_id
        )
    else:
        bot.sendMessage(
            text=text,
            reply_markup=reply_markup,
            chat_id=chat_id
        )


def start(bot, update):
    index(bot, update.message.from_user.id, update.message.chat.id)


def query_handler(bot, update):
    user_id = update.callback_query.from_user.id
    chat_id = update.callback_query.message.chat.id
    # message_id = update.callback_query.message.message_id
    data = json.loads(update.callback_query.data)
    if str(user_id) not in users.keys():
        logger.warning('Unknown user: %d', user_id)
        bot.sendMessage(
            text='User *{}* is not allowed here. Sorry.'.format(user_id),
            parse_mode='Markdown',
            chat_id=chat_id)
        return
    if data == 'history':
        logger.info('Sending history')
        history = get_history()
        text = 'History for the last 7 days:\n{}'.format('\n'.join(
            [
                'User {} executed {} on {} at {}'.format(
                    users.get(str(item['user_id']), str(user_id)),
                    item['action'],
                    item['host'],
                    datetime.fromtimestamp(item['ts'], tz).isoformat()
                ) for item in get_history()
            ]
        )) if len(history) else 'No history for the last 7 days'
        bot.sendMessage(text=text, chat_id=chat_id)
        time.sleep(2)
        index(
            bot,
            update.callback_query.from_user.id,
            update.callback_query.message.chat.id
        )
    elif 'action' in data and 'host' in data:
        try:
            ssh = paramiko.SSHClient()
            ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            host = hosts[data['host']]
            command = config['commands'][data['action']]
            if 'pkey' in host:
                ssh.connect(
                    host['host'],
                    username=host['user'],
                    key_filename=host['pkey'],
                    timeout=2
                )
            elif 'pass' in host:
                ssh.connect(
                    host['host'],
                    username=host['user'],
                    password=host['pass'],
                    look_for_keys=False,
                    timeout=2
                )
            _, stdout, stderr = ssh.exec_command(command)
            logger.info(
                'User %s executed %s on %s',
                users[str(user_id)], data['action'], data['host']
            )
            update_history({
                'user_id': user_id,
                'host': data['host'],
                'action': data['action'],
                'ts': floor(time.time())
            })
            out = stdout.read().decode("utf-8").strip()
            err = stderr.read().decode("utf-8").strip()
            bot.editMessageText(
                text='`{}`: `{}`'.format(command, out or err or 'OK'),
                parse_mode='Markdown',
                chat_id=update.callback_query.message.chat.id,
                message_id=update.callback_query.message.message_id
            )
        except Exception as e:
            logger.error(e)
            bot.editMessageText(
                text='*Error*: `{}`'.format(e),
                parse_mode='Markdown',
                chat_id=update.callback_query.message.chat.id,
                message_id=update.callback_query.message.message_id
            )
        time.sleep(2)
        index(
            bot,
            update.callback_query.from_user.id,
            update.callback_query.message.chat.id
        )
    elif 'host' in data:
        host = data['host']
        keyboard = [[]]
        row = keyboard[0]
        for action in config['commands']:
            row.append(telegram.InlineKeyboardButton(
                action, callback_data=json.dumps({
                    'host': host,
                    'action': action
                }))
            )
            if len(keyboard[-1]) % 2 == 0:
                row = []
                keyboard.append(row)
        keyboard.append([telegram.InlineKeyboardButton(
            'Cancel', callback_data=json.dumps({}))
        ])
        reply_markup = telegram.InlineKeyboardMarkup(keyboard)
        bot.editMessageText(
            text='Host: *{}*'.format(host),
            parse_mode='Markdown',
            chat_id=update.callback_query.message.chat.id,
            message_id=update.callback_query.message.message_id
        )
        bot.sendMessage(
            text='Please choose an action:',
            reply_markup=reply_markup,
            chat_id=update.callback_query.message.chat.id
        )
    else:
        bot.editMessageText(
            text='*Cancelled*',
            parse_mode='Markdown',
            chat_id=update.callback_query.message.chat.id,
            message_id=update.callback_query.message.message_id
        )
        time.sleep(2)
        index(
            bot,
            update.callback_query.from_user.id,
            update.callback_query.message.chat.id,
            update.callback_query.message.message_id
        )


updater = telegram.ext.Updater(config['bot']['token'])

updater.dispatcher.add_handler(telegram.ext.CommandHandler('start', start))
updater.dispatcher.add_handler(telegram.ext.CallbackQueryHandler(query_handler))

updater.dispatcher.add_error_handler(error)

updater.start_polling()
updater.idle()
