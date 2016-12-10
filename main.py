import configparser
import json

import logging
from time import sleep

import paramiko
import telegram
import telegram.ext

logging.basicConfig(format='%(asctime)s [%(levelname)s] %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

config = configparser.ConfigParser()
config.optionxform = str
config.read('config.ini')

users = list(map(int, config['bot']['users'].split(',')))
hosts = {
    section[len('host:'):]: dict(config._sections[section])
    for section in sorted(config.sections(), reverse=True)
    if section[:len('host:')] == 'host:'
    }


def error(bot, update, err):
    logger.error(err)


def index(bot, user_id, chat_id):
    if user_id not in users:
        logger.warning('Unknown user: {}'.format(user_id))
        bot.sendMessage(
            text='User *{}* is not allowed here. Sorry.'.format(user_id),
            parse_mode='Markdown',
            chat_id=chat_id)
        return
    keyboard = [[]]
    row = keyboard[0]
    for host in hosts:
        row.append(
            telegram.InlineKeyboardButton(host, callback_data=json.dumps({'host': host}))
        )
        if len(keyboard[-1]) % 2 == 0:
            row = []
            keyboard.append(row)
    reply_markup = telegram.InlineKeyboardMarkup(keyboard)
    text = 'Please choose a host:'
    bot.sendMessage(text=text, reply_markup=reply_markup, chat_id=chat_id)


def start(bot, update):
    index(bot, update.message.from_user.id, update.message.chat.id)


def query_handler(bot, update):
    user_id = update.callback_query.from_user.id
    chat_id = update.callback_query.message.chat.id
    message_id = update.callback_query.message.message_id
    data = json.loads(update.callback_query.data)
    if user_id not in users:
        logger.warning('Unknown user: {}'.format(user_id))
        bot.sendMessage(
            text='User *{}* is not allowed here. Sorry.'.format(user_id),
            parse_mode='Markdown',
            chat_id=chat_id)
        return
    if 'action' in data and 'host' in data:
        try:
            ssh = paramiko.SSHClient()
            ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            host = hosts[data['host']]
            command = config['commands'][data['action']]
            if 'pkey' in host:
                ssh.connect(host['host'], username=host['user'], key_filename=host['pkey'], timeout=2)
            elif 'pass' in host:
                logger.info('password')
                ssh.connect(host['host'], username=host['user'], password=host['pass'], look_for_keys=False, timeout=2)
            _, stdout, stderr = ssh.exec_command(command)
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
        sleep(2)
        index(bot, update.callback_query.from_user.id, update.callback_query.message.chat.id)
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
        sleep(2)
        index(bot, update.callback_query.from_user.id, update.callback_query.message.chat.id)


updater = telegram.ext.Updater(config['bot']['token'])

updater.dispatcher.add_handler(telegram.ext.CommandHandler('start', start))
updater.dispatcher.add_handler(telegram.ext.CallbackQueryHandler(query_handler))

updater.dispatcher.add_error_handler(error)

updater.start_polling()
updater.idle()
