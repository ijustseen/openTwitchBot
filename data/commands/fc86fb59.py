# ctx.user, ctx.args, ctx.message, ctx.channel, ctx.is_mod
# ctx.reply("текст") — сообщение в чат
# print(...) — строка в лог программы

def run(ctx):
    ctx.reply(f"Привет, {ctx.user}!")