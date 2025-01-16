import discord
from discord.ext import commands
from discord import app_commands
import random
import sqlite3
import datetime
import time
import logging

def load_token(file_path):
    try:
        with open(file_path, 'r') as file:
            token = file.read().strip()  # Remove any extra whitespace or newline characters
        return token
    except FileNotFoundError:
        print(f"Error: {file_path} not found.")
        return None

TOKEN = load_token('token.txt')
if not TOKEN:
    raise ValueError("Discord token not found. Ensure 'token.txt' contains the correct token.")

# USER ID for Admin command access
YOUR_USER_ID = 222736948966588416
# Admin role
FOOP_ROLE_NAME = 'foop'

# Channel Setup in case channel names get changed
RED_CHANNEL_NAME = "red"
BLUE_CHANNEL_NAME = "blu"

# Cooldown dictionary for updating mixes played vc_participation (mixes played)
participation_cooldown = {}
COOLDOWN_DURATION = 900  # set cooldown duration

# Cooldown dictionary for both med & captain spin commands
cooldowns = {}

# Manual medic cooldowns
manual_medic_cooldowns = {}

# Logging setup
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

# Discord bot Intents Setup
intents = discord.Intents.default()
intents.message_content = True  # Enables the bot to read message content
intents.voice_states = True  # Enables the bot to read voice states
intents.members = True  # Allows the bot to fetch member details

bot = commands.Bot(command_prefix='!', intents=intents)

# TF2 Quotes

# Function to load the questionable quotes of server members
def load_quotes(file_path):
    try:
        with open(file_path, 'r', encoding='utf-8') as file:
            quotes = [line.strip() for line in file.readlines() if line.strip()]  # Remove empty lines and strip whitespace
        return quotes
    except FileNotFoundError:
        print(f"Error: {file_path} not found.")
        return []

quotes = load_quotes('quotes.txt')
if not quotes:
    raise ValueError("Quotes not found. Ensure 'quotes.txt' contains at least one quote.")

# Database setup
conn = sqlite3.connect('stats_old.db')
c = conn.cursor()

# Create tables if they don't exist
c.execute('''CREATE TABLE IF NOT EXISTS spins (user_id INTEGER, role TEXT, count INTEGER)''')
c.execute('''CREATE TABLE IF NOT EXISTS vc_participation (user_id INTEGER, medic_spins INTEGER)''')
c.execute('''CREATE TABLE IF NOT EXISTS immunity (user_id INTEGER, role TEXT, timestamp DATETIME)''')
conn.commit()


def update_spin_count(user_id, role):
    c.execute('SELECT count FROM spins WHERE user_id = ? AND role = ?', (user_id, role))
    row = c.fetchone()
    if row:
        c.execute('UPDATE spins SET count = count + 1 WHERE user_id = ? AND role = ?', (user_id, role))
    else:
        c.execute('INSERT INTO spins (user_id, role, count) VALUES (?, ?, 1)', (user_id, role))
    conn.commit()


def update_vc_participation(user_ids): #vc_participation is the mixes played stat
    for user_id in user_ids:
        c.execute('SELECT medic_spins FROM vc_participation WHERE user_id = ?', (user_id,))
        row = c.fetchone()
        if row:
            c.execute('UPDATE vc_participation SET medic_spins = medic_spins + 1 WHERE user_id = ?', (user_id,))
        else:
            c.execute('INSERT INTO vc_participation (user_id, medic_spins) VALUES (?, 1)', (user_id,))
    conn.commit()


def set_immunity(user_id, role, hours):
    expiry = datetime.datetime.utcnow() + datetime.timedelta(hours=hours)
    c.execute('INSERT INTO immunity (user_id, role, timestamp) VALUES (?, ?, ?)', (user_id, role, expiry))
    conn.commit()


def check_immunity(user_id, role):
    c.execute('SELECT timestamp FROM immunity WHERE user_id = ? AND role = ?', (user_id, role))
    row = c.fetchone()
    if row:
        expiry = datetime.datetime.strptime(row[0], '%Y-%m-%d %H:%M:%S.%f')
        if expiry > datetime.datetime.utcnow():
            return True
        else:
            c.execute('DELETE FROM immunity WHERE user_id = ? AND role = ?', (user_id, role))
            conn.commit()
            return False
    return False


def remove_immunity(user_id, role):
    c.execute('DELETE FROM immunity WHERE user_id = ? AND role = ?', (user_id, role))
    conn.commit()

# Ready message to ensure but is booting properly
@bot.event
async def on_ready():
    print(f'Logged in as {bot.user.name} ({bot.user.id})')
    try:
        synced = await bot.tree.sync()
        print(f'Synced {len(synced)} command(s)')
    except Exception as e:
        print(e)


#Command list for members and admins according to their privileges
@bot.tree.command(name="command_list", description="List all available commands")
async def command_list(interaction: discord.Interaction):
    user_is_admin = interaction.user.id == YOUR_USER_ID or any(
        role.name == FOOP_ROLE_NAME for role in interaction.user.roles)

    commands = bot.tree.get_commands()

    general_commands = [
        command for command in commands
        if command.name not in ['manual_medic', 'grant_immunity', 'revoke_med_immunity', 'reduce_count', 'add_count', 'resetdatabases']
    ]
    admin_commands = [
        command for command in commands
        if command.name in ['manual_medic', 'grant_immunity', 'revoke_med_immunity', 'reduce_count', 'add_count', 'resetdatabases']
    ]

    response = "Here are the available commands:\n"
    response += "\n**General User Commands:**\n"
    for command in general_commands:
        response += f"/{command.name} - {command.description}\n"

    if user_is_admin:
        response += "\n**Admin Commands:**\n"
        for command in admin_commands:
            response += f"/{command.name} - {command.description}\n"

    await interaction.response.send_message(response, ephemeral=True)


# Update mixes played once both mixes channels are full (including cooldown to prevent abuse or accidental multiple counts)
@bot.event
async def on_voice_state_update(member, before, after):
    guild = member.guild
    red_channel = discord.utils.get(guild.voice_channels, name=RED_CHANNEL_NAME)
    blue_channel = discord.utils.get(guild.voice_channels, name=BLUE_CHANNEL_NAME)

    # Check if the update involves the red or blue channels
    if (before.channel == red_channel or before.channel == blue_channel or
        after.channel == red_channel or after.channel == blue_channel):

        red_members = [m for m in red_channel.members if not m.bot]
        blue_members = [m for m in blue_channel.members if not m.bot]

        # Log the number of members in each channel
        logging.info(f"Red channel members: {len(red_members)}")
        logging.info(f"Blue channel members: {len(blue_members)}")

        if len(red_members) == 6 and len(blue_members) == 6:
            logging.info("Both channels are full!")
            current_time = time.time()
            # Get user IDs from both teams and filter those who are not on cooldown
            user_ids_to_update = []
            for m in red_members + blue_members:
                last_update_time = participation_cooldown.get(m.id, 0)
                if current_time - last_update_time >= COOLDOWN_DURATION:
                    user_ids_to_update.append(m.id)
                    participation_cooldown[m.id] = current_time  # Update cooldown

            # Update the vc participation count for all members not on cooldown
            if user_ids_to_update:
                update_vc_participation(user_ids_to_update)


# Pick 2 random captains in current channel (includes gving immunity and checking immunity)
@bot.tree.command(name="spinforcaptains", description="Pick two random captains from your voice channel")
async def spinforcaptains(interaction: discord.Interaction):
    voice_state = interaction.user.voice
    if voice_state is None or voice_state.channel is None:
        await interaction.response.send_message("You must be in a voice channel to use this command.", ephemeral=True)
        return

    channel_id = voice_state.channel.id
    current_time = time.time()

    if channel_id in cooldowns and current_time - cooldowns[channel_id] < 120:
        time_left = int(120 - (current_time - cooldowns[channel_id]))
        await interaction.response.send_message(f"This command is on cooldown. Please wait {time_left} seconds.", ephemeral=True)
        return

    channel = voice_state.channel
    members = [member for member in channel.members if not member.bot]

    if len(members) < 10:
        await interaction.response.send_message("Not enough members in the voice channel to pick captains.", ephemeral=True)
        return

    eligible_members = [member for member in members if not check_immunity(member.id, 'captain')]
    if len(eligible_members) < 2:
        await interaction.response.send_message("Not enough eligible members to pick two captains (some may have immunity).")
        return

    captains = random.sample(eligible_members, 2)
    for captain in captains:
        update_spin_count(captain.id, 'captain')
        set_immunity(captain.id, 'captain', 3)  # Set immunity duration to 3 hours

    cooldowns[channel_id] = current_time
    await interaction.response.send_message(f"{captains[0].mention} and {captains[1].mention} are captains")


# Pick 2 random medics in current channel (includes gving immunity and checking immunity)
@bot.tree.command(name="spinformedic", description="Pick a random medic from your voice channel")
async def spinformedic(interaction: discord.Interaction):
    voice_state = interaction.user.voice
    if voice_state is None or voice_state.channel is None:
        await interaction.response.send_message("You must be in a voice channel to use this command.", ephemeral=True)
        return

    channel_id = voice_state.channel.id
    current_time = time.time()

    if channel_id in cooldowns and current_time - cooldowns[channel_id] < 120:
        time_left = int(120 - (current_time - cooldowns[channel_id]))
        await interaction.response.send_message(f"This command is on cooldown. Please wait {time_left} seconds.", ephemeral=True)
        return

    channel = voice_state.channel
    members = [member for member in channel.members if not member.bot]

    if len(members) < 6:
        await interaction.response.send_message("Not enough members in the voice channel to pick a medic.", ephemeral=True)
        return

    eligible_members = [member for member in members if not check_immunity(member.id, 'medic')]
    if not eligible_members:
        await interaction.response.send_message("No eligible members to be picked as medic (all have immunity).")
        return

    medic = random.choice(eligible_members)
    update_spin_count(medic.id, 'medic')
    set_immunity(medic.id, 'medic', 12)  # Set immunity duration to 12 hours
    cooldowns[channel_id] = current_time
    await interaction.response.send_message(f"{medic.mention} has been picked as medic and has been given medic immunity for the next 12 hours")


# Manual medic in case someone volunteers for med
@bot.tree.command(name="manual_medic", description="Manually set a user as medic and update stats")
async def manual_medic(interaction: discord.Interaction, user: discord.User, hours: int = 12):
    # Check if the user executing the command is the bot owner or has the FOOP role
    has_permission = interaction.user.id == YOUR_USER_ID or any(
        role.name == FOOP_ROLE_NAME for role in interaction.user.roles)

    if not has_permission:
        await interaction.response.send_message("You do not have permission to use this command.", ephemeral=True)
        return

    voice_state = user.voice
    if voice_state is None or voice_state.channel is None:
        await interaction.response.send_message("The specified user must be in a voice channel.", ephemeral=True)
        return

    channel = voice_state.channel
    members = [member for member in channel.members if not member.bot]

    if len(members) < 6:
        await interaction.response.send_message("There must be at least 6 people in the voice channel.", ephemeral=True)
        return

    channel_id = channel.id
    current_time = time.time()

    if channel_id in manual_medic_cooldowns and current_time - manual_medic_cooldowns[channel_id] < 120:
        time_left = int(120 - (current_time - manual_medic_cooldowns[channel_id]))
        await interaction.response.send_message(f"This command is on cooldown. Please wait {time_left} seconds.",
                                                ephemeral=True)
        return

    # Grant medic immunity
    set_immunity(user.id, 'medic', hours=hours)

    # Increment medic spun stat for the specified user
    c.execute('SELECT count FROM spins WHERE user_id = ? AND role = ?', (user.id, 'medic'))
    row = c.fetchone()
    if row:
        new_count = row[0] + 1
        c.execute('UPDATE spins SET count = ? WHERE user_id = ? AND role = ?', (new_count, user.id, 'medic'))
    else:
        new_count = 1
        c.execute('INSERT INTO spins (user_id, role, count) VALUES (?, ?, ?)', (user.id, 'medic', new_count))

    conn.commit()

    manual_medic_cooldowns[channel_id] = current_time

    await interaction.response.send_message(
        f"{user.display_name} has been manually set as medic, received medic immunity for {hours} hours, and stats have been updated.")



# Spin for maps (no one ever uses this tbh)
@bot.tree.command(name="spinformaps", description="Pick a random map from the pool")
async def spinformaps(interaction: discord.Interaction):
    maps_pool = [
        'cp_process', 'cp_snakewater', 'cp_sunshine', 'cp_gullywash', 'cp_sultry',
        'cp_granary', 'koth_product', 'koth_bagel', 'cp_metalworks', 'cp_entropy', 'cp_reckoner'
    ]
    selected_map = random.choice(maps_pool)
    await interaction.response.send_message(f"It's time to hit {selected_map}")


# Spin for 6's classes (never used before)
@bot.tree.command(name="spin_class", description="Randomly assign classes for a team")
async def spin_class(interaction: discord.Interaction):
    classes = ['Scout', 'Scout', 'Soldier', 'Soldier', 'Demoman', 'Medic']

    voice_state = interaction.user.voice
    if voice_state is None or voice_state.channel is None:
        await interaction.response.send_message("You must be in a voice channel to use this command.", ephemeral=True)
        return

    channel = voice_state.channel
    members = [member for member in channel.members if not member.bot]

    if len(members) < 6:
        await interaction.response.send_message(
            "There must be exactly 6 players in the voice channel to assign classes.", ephemeral=True)
        return

    random.shuffle(classes)
    response = "**Class Assignments for 6v6:**\n"
    for member, assigned_class in zip(members, classes):
        response += f"{member.display_name} - {assigned_class}\n"

    await interaction.response.send_message(response)


# Stats for single user (captain, medic, mixes)
@bot.tree.command(name="stats", description="Check the stats of a specified user")
async def stats(interaction: discord.Interaction, user: discord.User):
    c.execute('SELECT role, count FROM spins WHERE user_id = ?', (user.id,))
    spins = c.fetchall()
    c.execute('SELECT medic_spins FROM vc_participation WHERE user_id = ?', (user.id,))
    vc_participation = c.fetchone()

    if not spins and not vc_participation:
        await interaction.response.send_message(f"No stats found for {user.mention}")
        return

    # Initialize the response with zeros
    medic_spins = 0
    captain_spins = 0
    vc_medic_spins = 0 if vc_participation is None else vc_participation[0]

    for role, count in spins:
        if role == 'medic':
            medic_spins = count
        elif role == 'captain':
            captain_spins = count

    response = (
        f"Stats for {user.mention}:\n"
        f"- Mixes played: {vc_medic_spins}\n"
        f"- Medic Spins won: {medic_spins}\n"
        f"- Captain Spins won: {captain_spins}"
    )

    await interaction.response.send_message(response)


# Leadboard top 10
@bot.tree.command(name="leaderboard", description="Show the leaderboard")
@app_commands.describe(role="The role to show the leaderboard for")
@app_commands.choices(role=[
    app_commands.Choice(name="captain", value="captain"),
    app_commands.Choice(name="medic", value="medic"),
    app_commands.Choice(name="mixes", value="vc_medic")
])
async def leaderboard(interaction: discord.Interaction, role: app_commands.Choice[str]):
    if role.value == 'vc_medic':
        c.execute('SELECT user_id, medic_spins FROM vc_participation ORDER BY medic_spins DESC LIMIT 10')
        leaderboard = c.fetchall()
        response = "Top 10 Mixes Played:\n"
        for i, (user_id, count) in enumerate(leaderboard, start=1):
            try:
                member = await interaction.guild.fetch_member(user_id)
                display_name = member.display_name
            except:
                display_name = f"User ID: {user_id}"
            response += f"{i}. {display_name}: {count} mixes\n"
    else:
        c.execute('SELECT user_id, count FROM spins WHERE role = ? ORDER BY count DESC LIMIT 10', (role.value,))
        leaderboard = c.fetchall()
        response = f"Top 10 {role.name.capitalize()} Spins:\n"
        for i, (user_id, count) in enumerate(leaderboard, start=1):
            try:
                member = await interaction.guild.fetch_member(user_id)
                display_name = member.display_name
            except:
                display_name = f"User ID: {user_id}"
            response += f"{i}. {display_name}: {count}\n"

    if not leaderboard:
        await interaction.response.send_message(f"No leaderboard data for {role.name}.", ephemeral=True)
        return

    await interaction.response.send_message(response)


# Leaderboard all
@bot.tree.command(name="leaderboard_all", description="Show the full leaderboard for all users with at least 1 in their stats")
@app_commands.describe(role="The role to show the leaderboard for")
@app_commands.choices(role=[
    app_commands.Choice(name="captain", value="captain"),
    app_commands.Choice(name="medic", value="medic"),
    app_commands.Choice(name="mixes", value="vc_medic")
])
async def leaderboard_all(interaction: discord.Interaction, role: app_commands.Choice[str]):
    await interaction.response.defer()  # Acknowledge the interaction first

    if role.value == 'vc_medic':
        c.execute('SELECT user_id, medic_spins FROM vc_participation WHERE medic_spins > 0 ORDER BY medic_spins DESC')
        leaderboard = c.fetchall()
        response = "Mixes Played:\n"
        for i, (user_id, count) in enumerate(leaderboard, start=1):
            try:
                member = await interaction.guild.fetch_member(user_id)
                display_name = member.display_name
            except:
                display_name = f"User ID: {user_id}"
            response += f"{i}. {display_name}: {count} mixes\n"
    else:
        c.execute('SELECT user_id, count FROM spins WHERE role = ? AND count > 0 ORDER BY count DESC', (role.value,))
        leaderboard = c.fetchall()
        response = f"{role.name.capitalize()} Spins:\n"
        for i, (user_id, count) in enumerate(leaderboard, start=1):
            try:
                member = await interaction.guild.fetch_member(user_id)
                display_name = member.display_name
            except:
                display_name = f"User ID: {user_id}"
            response += f"{i}. {display_name}: {count}\n"

    if not leaderboard:
        await interaction.followup.send(f"No leaderboard data for {role.name}.", ephemeral=True)
    else:
        await interaction.followup.send(response)


#  Reset databases, can only be used by Author, was used in early setup
@bot.tree.command(name="resetdatabases", description="Reset all stats and data")
async def resetdatabases(interaction: discord.Interaction):
    if interaction.user.id != YOUR_USER_ID:
        await interaction.response.send_message("You do not have permission to use this command.", ephemeral=True)
        return

    c.execute('DELETE FROM spins')
    c.execute('DELETE FROM vc_participation')
    c.execute('DELETE FROM immunity')
    conn.commit()
    await interaction.response.send_message("All databases have been reset.", ephemeral=True)


# Revoking med immunity for specific user (Mostly used for Vitun)
@bot.tree.command(name="revoke_med_immunity", description="Revoke medic immunity of a specified user")
async def revoke_med_immunity(interaction: discord.Interaction, user: discord.User):
    has_permission = interaction.user.id == YOUR_USER_ID or any(
        role.name == FOOP_ROLE_NAME for role in interaction.user.roles)

    if not has_permission:
        await interaction.response.send_message("You do not have permission to use this command.", ephemeral=True)
        return

    remove_immunity(user.id, 'medic')

    # Update medic spins count
    c.execute('SELECT count FROM spins WHERE user_id = ? AND role = ?', (user.id, 'medic'))
    row = c.fetchone()
    if row:
        new_count = max(row[0] - 1, 0)
        c.execute('UPDATE spins SET count = ? WHERE user_id = ? AND role = ?', (new_count, user.id, 'medic'))
        conn.commit()
        await interaction.response.send_message(
            f"Medic immunity has been revoked for {user.display_name}. New medic count is {new_count}.")
    else:
        await interaction.response.send_message(f"{user.display_name} does not have any medic spins to reduce.",
                                                ephemeral=True)


# Manual Immunity grant for people volunteering med (Legacy, mostly replaced by manual medic command)
@bot.tree.command(name="grant_immunity", description="Grant medic immunity to a specified user")
async def grant_immunity(interaction: discord.Interaction, user: discord.User, hours: int = 12):
    has_permission = interaction.user.id == YOUR_USER_ID or any(
        role.name == FOOP_ROLE_NAME for role in interaction.user.roles)

    if not has_permission:
        await interaction.response.send_message("You do not have permission to use this command.", ephemeral=True)
        return

    set_immunity(user.id, 'medic', hours=hours)

    # Update medic spins count
    c.execute('SELECT count FROM spins WHERE user_id = ? AND role = ?', (user.id, 'medic'))
    row = c.fetchone()
    if row:
        new_count = row[0] + 1
        c.execute('UPDATE spins SET count = ? WHERE user_id = ? AND role = ?', (new_count, user.id, 'medic'))
    else:
        new_count = 1
        c.execute('INSERT INTO spins (user_id, role, count) VALUES (?, ?, ?)', (user.id, 'medic', new_count))
    conn.commit()

    await interaction.response.send_message(
        f"Medic immunity has been granted to {user.display_name} for {hours} hours. New medic count is {new_count}.")


# Check users that currently have med immunity
@bot.tree.command(name="check_med_immunity", description="List all users with medic immunity and remaining time")
async def check_all_med_immunity(interaction: discord.Interaction):
    c.execute('SELECT user_id, timestamp FROM immunity WHERE role = ?', ('medic',))
    rows = c.fetchall()
    if not rows:
        await interaction.response.send_message("No users currently have medic immunity.")
        return

    response = "Users with Medic Immunity:\n"
    for user_id, expiry_str in rows:
        expiry = datetime.datetime.strptime(expiry_str, '%Y-%m-%d %H:%M:%S.%f')
        if expiry > datetime.datetime.utcnow():
            time_left = expiry - datetime.datetime.utcnow()
            hours, remainder = divmod(int(time_left.total_seconds()), 3600)
            minutes, seconds = divmod(remainder, 60)
            member = await interaction.guild.fetch_member(user_id)
            display_name = member.display_name if member else f"User ID: {user_id}"
            response += f"- {display_name}: {hours} hours and {minutes} minutes remaining\n"

    await interaction.response.send_message(response)


# Check users that currently have captain immunity
@bot.tree.command(name="check_captain_immunity", description="List all users with captain immunity and remaining time")
async def check_captain_immunity(interaction: discord.Interaction):
    c.execute('SELECT user_id, timestamp FROM immunity WHERE role = ?', ('captain',))
    rows = c.fetchall()
    if not rows:
        await interaction.response.send_message("No users currently have captain immunity.")
        return

    response = "Users with Captain Immunity:\n"
    for user_id, expiry_str in rows:
        expiry = datetime.datetime.strptime(expiry_str, '%Y-%m-%d %H:%M:%S.%f')
        if expiry > datetime.datetime.utcnow():
            time_left = expiry - datetime.datetime.utcnow()
            hours, remainder = divmod(int(time_left.total_seconds()), 3600)
            minutes, seconds = divmod(remainder, 60)
            member = await interaction.guild.fetch_member(user_id)
            display_name = member.display_name if member else f"User ID: {user_id}"
            response += f"- {display_name}: {hours} hours and {minutes} minutes remaining\n"

    await interaction.response.send_message(response)


# Manual reducing counts in database, basically bug fixing when bot goes wrong
@bot.tree.command(name="reduce_count", description="Reduce the count for a specified role by a specified amount")
@app_commands.describe(role="The role to reduce the count for")
@app_commands.choices(role=[
    app_commands.Choice(name="captain", value="captain"),
    app_commands.Choice(name="medic", value="medic"),
    app_commands.Choice(name="mixes", value="vc_medic")
])

async def reduce_count(interaction: discord.Interaction, user: discord.User, role: app_commands.Choice[str], count: int):
    # Check if the user executing the command is the bot owner
    if interaction.user.id != YOUR_USER_ID:
        await interaction.response.send_message("You do not have permission to use this command.", ephemeral=True)
        return

    if count <= 0:
        await interaction.response.send_message("Count must be greater than zero.", ephemeral=True)
        return

    if role.value == 'vc_medic':
        c.execute('SELECT medic_spins FROM vc_participation WHERE user_id = ?', (user.id,))
        row = c.fetchone()
        if row:
            new_count = max(row[0] - count, 0)
            c.execute('UPDATE vc_participation SET medic_spins = ? WHERE user_id = ?', (new_count, user.id))
            conn.commit()
            await interaction.response.send_message(f"Reduced {user.display_name}'s mixes played count by {count}. New count is {new_count}.")
        else:
            await interaction.response.send_message(f"{user.display_name} does not have any mixes played count to reduce.", ephemeral=True)
    else:
        c.execute('SELECT count FROM spins WHERE user_id = ? AND role = ?', (user.id, role.value))
        row = c.fetchone()
        if row:
            new_count = max(row[0] - count, 0)
            c.execute('UPDATE spins SET count = ? WHERE user_id = ? AND role = ?', (new_count, user.id, role.value))
            conn.commit()
            await interaction.response.send_message(f"Reduced {user.display_name}'s {role.name} spins by {count}. New count is {new_count}.")
        else:
            await interaction.response.send_message(f"{user.display_name} does not have any {role.name} spins to reduce.", ephemeral=True)


# Manual adding counts in database, basically bug fixing when bot goes wrong
@bot.tree.command(name="add_count", description="Add to the count for a specified role by a specified amount")
@app_commands.describe(role="The role to add the count for")
@app_commands.choices(role=[
    app_commands.Choice(name="captain", value="captain"),
    app_commands.Choice(name="medic", value="medic"),
    app_commands.Choice(name="mixes", value="vc_medic")
])
async def add_count(interaction: discord.Interaction, user: discord.User, role: app_commands.Choice[str], count: int):
    # Check if the user executing the command is the bot owner
    if interaction.user.id != YOUR_USER_ID:
        await interaction.response.send_message("You do not have permission to use this command.", ephemeral=True)
        return

    if count <= 0:
        await interaction.response.send_message("Count must be greater than zero.", ephemeral=True)
        return

    if role.value == 'vc_medic':
        c.execute('SELECT medic_spins FROM vc_participation WHERE user_id = ?', (user.id,))
        row = c.fetchone()
        if row:
            new_count = row[0] + count
            c.execute('UPDATE vc_participation SET medic_spins = ? WHERE user_id = ?', (new_count, user.id))
        else:
            new_count = count
            c.execute('INSERT INTO vc_participation (user_id, medic_spins) VALUES (?, ?)', (user.id, new_count))
    else:
        c.execute('SELECT count FROM spins WHERE user_id = ? AND role = ?', (user.id, role.value))
        row = c.fetchone()
        if row:
            new_count = row[0] + count
            c.execute('UPDATE spins SET count = ? WHERE user_id = ? AND role = ?', (new_count, user.id, role.value))
        else:
            new_count = count
            c.execute('INSERT INTO spins (user_id, role, count) VALUES (?, ?, ?)', (user.id, role.value, new_count))
    conn.commit()
    await interaction.response.send_message(f"Added {count} to {user.display_name}'s {role.name} count. New count is {new_count}.")


# Pulls a funny quote from the quote file
@bot.tree.command(name="quote", description="Get a random TF2-related quote")
async def quote(interaction: discord.Interaction):
    selected_quote = random.choice(quotes)
    await interaction.response.send_message(selected_quote)

# Run the bot
bot.run(TOKEN)