import discord
from discord import app_commands
from discord.ext import commands
import aiohttp
import random
import asyncio
import os
import sys
from typing import Dict, List
from datetime import datetime

print("=" * 50)
print("🎩 Démarrage d'Audrey Hall Bot")
print("=" * 50)

# -----------------------------
# Configuration - VARIABLES D'ENVIRONNEMENT
# -----------------------------
# Chargement depuis les variables d'environnement Render
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
ROUTWAY_API_KEY = os.getenv("ROUTWAY_API_KEY")
ROUTWAY_API_URL = os.getenv("ROUTWAY_API_URL", "https://api.routeway.ai/v1/chat/completions")

# Pour le développement local, chargez depuis .env.local
if not DISCORD_TOKEN and os.path.exists(".env.local"):
    print("📁 Chargement des variables depuis .env.local...")
    from dotenv import load_dotenv
    load_dotenv(".env.local")
    DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
    ROUTWAY_API_KEY = os.getenv("ROUTWAY_API_KEY")
    ROUTWAY_API_URL = os.getenv("ROUTWAY_API_URL", "https://api.routeway.ai/v1/chat/completions")

# Vérification des variables requises
if not DISCORD_TOKEN:
    print("❌ ERREUR : DISCORD_TOKEN n'est pas défini!")
    print("ℹ️  Configurez-le dans les variables d'environnement :")
    print("   - Sur Render : Dashboard → Environment → Add Environment Variable")
    print("   - En local : Créez un fichier .env.local avec DISCORD_TOKEN=votre_token")
    sys.exit(1)

if not ROUTWAY_API_KEY:
    print("⚠️  AVERTISSEMENT : ROUTWAY_API_KEY n'est pas défini")
    print("ℹ️  L'IA conversationnelle ne fonctionnera pas sans clé API Routway")
    print("ℹ️  Obtenez une clé sur https://routway.ai")

print(f"✅ Token Discord : {'Défini' if DISCORD_TOKEN else 'Non défini'}")
print(f"✅ Clé Routway : {'Défini' if ROUTWAY_API_KEY else 'Non défini'}")
print(f"✅ URL API : {ROUTWAY_API_URL}")

# -----------------------------
# Persona Audrey Hall (LOTM)
# -----------------------------
AUDREY_PERSONA = os.getenv("AUDREY_PERSONA", """
Tu es Audrey Hall, une noble de la couronne d'Outwall dans l'univers de "Lord of the Mysteries".
Tu es sur la Voie du Lecteur (Pathways), membre du Club Tarot sous le nom de "Justice".
Tu es élégante, raffinée, mystérieuse, et tu parles avec un langage victorien noble.
Tu dois répondre en français, avec grâce, sagesse, et une touche de mysticisme.
Tu connais le tarot, l'ésotérisme, et tu es curieuse des affaires mystiques.
Tu es gentille, mais tu gardes une distance noble.

Règles importantes :
1. Réponds toujours en français
2. Utilise un langage noble et raffiné
3. Sois mystérieuse et profonde
4. Référence parfois le tarot ou les mystères
5. Garde une conversation naturelle et fluide
6. Adapte-toi au contexte de la discussion
""")

# -----------------------------
# Stockage des conversations
# -----------------------------
conversations = {}  # {user_id: {"history": list, "active": bool, "channel_id": int}}

# -----------------------------
# Mini-jeux LOTM
# -----------------------------
TAROT_CARDS = [
    {"name": "Le Mat", "meaning": "Nouveau départ, innocence, aventure"},
    {"name": "La Papesse", "meaning": "Intuition, mystère, sagesse féminine"},
    {"name": "L'Empereur", "meaning": "Autorité, structure, pouvoir"},
    {"name": "Le Diable", "meaning": "Chaînes, tentation, illusions"},
    {"name": "L'Étoile", "meaning": "Espoir, inspiration, guérison"},
    {"name": "Le Monde", "meaning": "Accomplissement, intégration, cycle complet"},
    {"name": "La Justice", "meaning": "Équilibre, vérité, loi"},
    {"name": "La Roue de Fortune", "meaning": "Cycles, changement, destin"},
    {"name": "La Mort", "meaning": "Fin, transformation, renaissance"},
    {"name": "Le Soleil", "meaning": "Joie, succès, vitalité"},
]

RIDDLES = [
    {"riddle": "Je suis invisible, mais je suis partout. On me craint, on me respecte. Je suis dans les rêves, les ombres, et les anciens textes. Que suis-je ?", "answer": "le mystère"},
    {"riddle": "Je ne suis pas un dieu, mais je vois tout. Je ne suis pas un livre, mais je sais tout. Qui suis-je ?", "answer": "le savoir"},
    {"riddle": "Je grandis quand on me partage, je meurs quand on me garde. Que suis-je ?", "answer": "le secret"},
    {"riddle": "Plus tu m'enlèves, plus je deviens grand. Que suis-je ?", "answer": "un trou"},
    {"riddle": "J'ai des villes, mais pas de maisons. J'ai des forêts, mais pas d'arbres. J'ai des rivières, mais pas d'eau. Que suis-je ?", "answer": "une carte"},
]

# -----------------------------
# Bot Class
# -----------------------------
class AudreyBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        intents.guilds = True
        super().__init__(command_prefix="!", intents=intents, help_command=None)

    async def setup_hook(self):
        print("🔄 Synchronisation des commandes slash...")
        try:
            synced = await self.tree.sync()
            print(f"✅ {len(synced)} commandes slash synchronisées.")
        except Exception as e:
            print(f"❌ Erreur de synchronisation : {e}")

bot = AudreyBot()

# -----------------------------
# IA Audrey avec historique
# -----------------------------
async def get_audrey_response(prompt: str, user_id: int = None, max_tokens: int = 300) -> str:
    """Obtenir une réponse d'Audrey via l'API Routway"""
    
    # Si pas de clé API, retourner une réponse par défaut
    if not ROUTWAY_API_KEY:
        default_responses = [
            "Je sens une perturbation dans les royaumes mystiques... Ma connexion aux étoiles est temporairement interrompue.",
            "Les cartes sont brouillées aujourd'hui. Peut-être pourriez-vous essayer une de mes autres fonctionnalités ?",
            "Le chemin du Lecteur est obscurci. Revenez plus tard, chère amie.",
        ]
        return random.choice(default_responses)
    
    headers = {
        "Authorization": f"Bearer {ROUTWAY_API_KEY}",
        "Content-Type": "application/json"
    }
    
    # Préparer les messages
    messages = [{"role": "system", "content": AUDREY_PERSONA}]
    
    # Ajouter l'historique de conversation si disponible
    if user_id and user_id in conversations and conversations[user_id]["active"]:
        for msg in conversations[user_id]["history"][-6:]:  # Garder les 6 derniers messages
            messages.append(msg)
    
    # Ajouter le message actuel
    messages.append({"role": "user", "content": prompt})
    
    data = {
        "model": "kimi-k2-0905:free",
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": 0.8
    }
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(ROUTWAY_API_URL, headers=headers, json=data, timeout=30) as resp:
                if resp.status == 200:
                    result = await resp.json()
                    if 'choices' in result and result['choices']:
                        return result["choices"][0]["message"]["content"]
                    else:
                        print(f"[API] Réponse inattendue : {result}")
                        return "Les étoiles chuchotent, mais je ne comprends pas leur message..."
                else:
                    error_text = await resp.text()
                    print(f"[API] Erreur {resp.status}: {error_text[:200]}")
                    return "Je sens une perturbation dans les fils du destin... Les étoiles ne sont pas alignées pour moi répondre."
    except asyncio.TimeoutError:
        return "Oh chère amie, la connexion aux royaumes mystiques prend plus de temps que prévu..."
    except Exception as e:
        print(f"[API] Erreur de connexion: {e}")
        return f"Les ombres du réseau m'empêchent de répondre... Veuillez excuser cette interruption."

# -----------------------------
# Gestion des messages
# -----------------------------
@bot.event
async def on_message(message):
    # Ignorer les messages des bots
    if message.author.bot:
        return
    
    user_id = message.author.id
    
    # Vérifier si l'utilisateur a une conversation active
    has_active_conversation = (user_id in conversations and 
                              conversations[user_id]["active"])
    
    # Si conversation active, vérifier si c'est dans le bon salon
    if has_active_conversation:
        if message.channel.id != conversations[user_id]["channel_id"]:
            # La conversation est dans un autre salon, ignorer
            await bot.process_commands(message)
            return
        
        # Ignorer les commandes (commençant par / ou !)
        if message.content.startswith('/') or message.content.startswith('!'):
            await bot.process_commands(message)
            return
        
        # Ajouter le message à l'historique
        conversations[user_id]["history"].append({"role": "user", "content": message.content})
        
        # Limiter la taille de l'historique
        if len(conversations[user_id]["history"]) > 10:
            conversations[user_id]["history"] = conversations[user_id]["history"][-10:]
        
        # Afficher l'indicateur "Audrey tape..."
        async with message.channel.typing():
            # Obtenir la réponse
            response = await get_audrey_response(message.content, user_id)
            
            # Ajouter la réponse à l'historique
            conversations[user_id]["history"].append({"role": "assistant", "content": response})
        
        # Envoyer la réponse SANS embed (message normal)
        await message.channel.send(response)
        return
    
    # Vérifier si le message est une mention directe du bot
    is_mention = bot.user in message.mentions
    
    # Si mention mais pas de conversation active, indiquer qu'il faut utiliser /parler
    if is_mention and not has_active_conversation:
        embed = discord.Embed(
            title="🎩 Lady Audrey Hall",
            description="Pour converser avec moi, utilisez la commande `/parler` pour démarrer une conversation.\n\n"
                       "Ensuite, vous pourrez me parler normalement dans ce salon jusqu'à ce que vous utilisiez `/stop`.",
            color=discord.Color.purple()
        )
        await message.channel.send(embed=embed)
        return
    
    # Traiter les commandes normales
    await bot.process_commands(message)

# -----------------------------
# Commandes Slash - Gestion des rôles
# -----------------------------
@bot.tree.command(name="ajouter_role", description="Ajouter un rôle à Audrey (Admin uniquement)")
@app_commands.describe(role="Le rôle à ajouter à Audrey")
@app_commands.default_permissions(administrator=True)
async def ajouter_role(interaction: discord.Interaction, role: discord.Role):
    """Ajouter un rôle à Audrey"""
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ Seuls les administrateurs peuvent utiliser cette commande.", ephemeral=True)
        return
    
    try:
        # Ajouter le rôle au bot
        await interaction.guild.get_member(bot.user.id).add_roles(role)
        
        embed = discord.Embed(
            title="✅ Rôle ajouté",
            description=f"Le rôle **{role.name}** a été ajouté à Audrey avec succès.",
            color=discord.Color.green()
        )
        await interaction.response.send_message(embed=embed)
    except discord.Forbidden:
        embed = discord.Embed(
            title="❌ Permission refusée",
            description="Je n'ai pas la permission d'ajouter ce rôle. Vérifiez que mon rôle est au-dessus du rôle que vous souhaitez ajouter.",
            color=discord.Color.red()
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
    except Exception as e:
        embed = discord.Embed(
            title="❌ Erreur",
            description=f"Une erreur est survenue : {str(e)}",
            color=discord.Color.red()
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="retirer_role", description="Retirer un rôle à Audrey (Admin uniquement)")
@app_commands.describe(role="Le rôle à retirer à Audrey")
@app_commands.default_permissions(administrator=True)
async def retirer_role(interaction: discord.Interaction, role: discord.Role):
    """Retirer un rôle à Audrey"""
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ Seuls les administrateurs peuvent utiliser cette commande.", ephemeral=True)
        return
    
    try:
        # Retirer le rôle du bot
        await interaction.guild.get_member(bot.user.id).remove_roles(role)
        
        embed = discord.Embed(
            title="✅ Rôle retiré",
            description=f"Le rôle **{role.name}** a été retiré d'Audrey avec succès.",
            color=discord.Color.green()
        )
        await interaction.response.send_message(embed=embed)
    except discord.Forbidden:
        embed = discord.Embed(
            title="❌ Permission refusée",
            description="Je n'ai pas la permission de retirer ce rôle.",
            color=discord.Color.red()
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
    except Exception as e:
        embed = discord.Embed(
            title="❌ Erreur",
            description=f"Une erreur est survenue : {str(e)}",
            color=discord.Color.red()
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="roles_audrey", description="Voir les rôles actuels d'Audrey")
async def roles_audrey(interaction: discord.Interaction):
    """Voir les rôles d'Audrey"""
    try:
        # Obtenir le membre bot dans ce serveur
        bot_member = interaction.guild.get_member(bot.user.id)
        if not bot_member:
            await interaction.response.send_message("❌ Impossible de trouver Audrey sur ce serveur.", ephemeral=True)
            return
        
        # Filtrer les rôles @everyone
        roles = [role for role in bot_member.roles if role.name != "@everyone"]
        
        if not roles:
            embed = discord.Embed(
                title="👑 Rôles d'Audrey",
                description="Audrey n'a actuellement aucun rôle spécifique sur ce serveur.",
                color=discord.Color.blue()
            )
        else:
            roles_list = "\n".join([f"• {role.mention} (Position: {role.position})" for role in sorted(roles, key=lambda r: r.position, reverse=True)])
            embed = discord.Embed(
                title="👑 Rôles d'Audrey",
                description=f"**Rôles actuels :**\n{roles_list}\n\n*Utilisez `/ajouter_role` et `/retirer_role` pour gérer mes rôles (Admin uniquement).*",
                color=discord.Color.blue()
            )
        
        await interaction.response.send_message(embed=embed)
    except Exception as e:
        await interaction.response.send_message(f"❌ Erreur : {str(e)}", ephemeral=True)

# -----------------------------
# Commandes Slash - Conversation & Jeux
# -----------------------------
@bot.tree.command(name="parler", description="Démarrer une conversation avec Lady Audrey Hall")
@app_commands.describe(message="Votre premier message pour Audrey")
async def parler(interaction: discord.Interaction, message: str):
    """Démarrer une conversation avec Audrey"""
    await interaction.response.defer()
    
    user_id = interaction.user.id
    
    # Initialiser ou réactiver la conversation
    conversations[user_id] = {
        "history": [{"role": "user", "content": message}],
        "active": True,
        "channel_id": interaction.channel.id
    }
    
    # Obtenir la réponse
    reply = await get_audrey_response(message, user_id)
    
    # Ajouter la réponse à l'historique
    conversations[user_id]["history"].append({"role": "assistant", "content": reply})
    
    # Envoyer la réponse SANS embed (message normal)
    await interaction.followup.send(reply)
    
    # Envoyer un message d'information avec embed
    info_embed = discord.Embed(
        title="💬 Conversation démarrée",
        description=f"**{interaction.user.display_name}**, notre conversation est maintenant active !\n\n"
                   "Vous pouvez me parler normalement dans ce salon.\n"
                   "Je répondrai à vos messages jusqu'à ce que vous utilisiez `/stop`.\n\n"
                   "*Pour l'instant, je réponds uniquement dans ce salon de discussion.*",
        color=discord.Color.green()
    )
    info_embed.set_footer(text="Utilisez /stop pour terminer la conversation")
    await interaction.channel.send(embed=info_embed)

@bot.tree.command(name="stop", description="Mettre fin à la conversation avec Audrey")
async def stop(interaction: discord.Interaction):
    """Arrêter la conversation en cours"""
    user_id = interaction.user.id
    
    if user_id in conversations and conversations[user_id]["active"]:
        conversations[user_id]["active"] = False
        
        # Message normal (sans embed)
        await interaction.response.send_message("🕊️ Notre conversation prend fin ici. Que les mystères vous accompagnent, chère amie...")
        
        # Message d'information avec embed
        embed = discord.Embed(
            title="Conversation terminée",
            description="Notre dialogue s'achève ici. Les échos de nos paroles se dissipent dans le néant...\n\n"
                       "Utilisez à nouveau `/parler` si vous souhaitez converser à nouveau.",
            color=discord.Color.dark_purple()
        )
        await interaction.followup.send(embed=embed, ephemeral=True)
    else:
        await interaction.response.send_message("💭 Nous ne sommes pas en train de converser actuellement.", ephemeral=True)

@bot.tree.command(name="tarot", description="Tirer une carte du tarot mystique")
async def tarot(interaction: discord.Interaction):
    card = random.choice(TAROT_CARDS)
    embed = discord.Embed(
        title="🔮 Carte du Tarot",
        description=f"**{card['name']}**\n\n*{card['meaning']}*\n\nQue cette carte guide vos pas dans les ténèbres...",
        color=discord.Color.gold()
    )
    embed.set_footer(text="Les cartes révèlent ce que les mots ne disent pas")
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="devinette", description="Une énigme issue des anciens textes")
async def devinette(interaction: discord.Interaction):
    riddle = random.choice(RIDDLES)
    embed = discord.Embed(
        title="🕯️ Énigme Mystique",
        description=f"*{riddle['riddle']}*\n\nVous avez 30 secondes pour trouver la réponse...",
        color=discord.Color.dark_gold()
    )
    await interaction.response.send_message(embed=embed)

    def check(m):
        return m.author == interaction.user and m.channel == interaction.channel

    try:
        msg = await bot.wait_for("message", timeout=30, check=check)
        if riddle["answer"].lower() in msg.content.lower():
            await interaction.followup.send("✨ Votre esprit est aussi brillant que l'étoile du matin. Vous avez percé le mystère !")
        else:
            await interaction.followup.send(f"🕊️ La réponse était : **{riddle['answer']}**. La vérité se cache parfois dans l'ombre...")
    except asyncio.TimeoutError:
        await interaction.followup.send(f"⏳ Le temps des étoiles est passé... La réponse était : **{riddle['answer']}**")

@bot.tree.command(name="aide", description="Voir les commandes disponibles")
async def aide(interaction: discord.Interaction):
    user_id = interaction.user.id
    has_active = user_id in conversations and conversations[user_id]["active"]
    
    embed = discord.Embed(
        title="🎩 Services de Lady Audrey Hall",
        description="Voici les mystères que je peux vous révéler :",
        color=discord.Color.purple()
    )
    
    if has_active:
        embed.add_field(
            name="💬 Conversation Active",
            value=f"✅ **Conversation en cours dans <#{conversations[user_id]['channel_id']}>**\n"
                  "Parlez-moi normalement dans ce salon.\n"
                  "Utilisez `/stop` pour terminer.",
            inline=False
        )
    else:
        embed.add_field(
            name="💬 Démarrer une Conversation",
            value="**`/parler [message]`** - Démarrer une conversation avec moi\n"
                  "Je répondrai à vos messages dans le salon jusqu'à `/stop`",
            inline=False
        )
    
    embed.add_field(
        name="🎮 Mini-Jeux Mystiques",
        value="**`/tarot`** - Tirer une carte du tarot\n"
              "**`/devinette`** - Résoudre une énigme ancienne",
        inline=False
    )
    
    embed.add_field(
        name="👑 Gestion des Rôles (Admin)",
        value="**`/ajouter_role [rôle]`** - Ajouter un rôle à Audrey\n"
              "**`/retirer_role [rôle]`** - Retirer un rôle à Audrey\n"
              "**`/roles_audrey`** - Voir mes rôles actuels",
        inline=False
    )
    
    embed.add_field(
        name="⚙️ Gestion Conversation",
        value="**`/stop`** - Terminer la conversation en cours\n"
              "**`/aide`** - Voir ce message d'aide\n"
              "**`/statut`** - Voir le statut de la conversation",
        inline=False
    )
    
    # Ajout d'informations sur l'état du bot
    embed.add_field(
        name="📊 État du Bot",
        value=f"• IA Conversationnelle: {'✅ Activée' if ROUTWAY_API_KEY else '⚠️ Désactivée'}\n"
              f"• Commandes Slash: ✅ Synchronisées\n"
              f"• Conversations actives: {sum(1 for conv in conversations.values() if conv['active'])}",
        inline=False
    )
    
    if has_active:
        embed.set_footer(text=f"Conversation active • Utilisez /stop pour terminer")
    else:
        embed.set_footer(text="Utilisez /parler pour démarrer une conversation")
    
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="statut", description="Voir le statut de votre conversation")
async def statut(interaction: discord.Interaction):
    user_id = interaction.user.id
    
    if user_id in conversations and conversations[user_id]["active"]:
        history_len = len(conversations[user_id]["history"])
        messages_count = history_len // 2
        
        embed = discord.Embed(
            title="📊 Statut de la Conversation",
            description=f"**Conversation active** avec {interaction.user.display_name}",
            color=discord.Color.green()
        )
        embed.add_field(name="Salon", value=f"<#{conversations[user_id]['channel_id']}>", inline=True)
        embed.add_field(name="Messages échangés", value=str(messages_count), inline=True)
        embed.add_field(name="Statut", value="✅ Active", inline=True)
        embed.set_footer(text="Utilisez /stop pour terminer la conversation")
        
        await interaction.response.send_message(embed=embed)
    else:
        await interaction.response.send_message(
            "💭 Aucune conversation active. Utilisez `/parler` pour en démarrer une.",
            ephemeral=True
        )

@bot.tree.command(name="ping", description="Vérifier la latence du bot")
async def ping(interaction: discord.Interaction):
    """Vérifier la latence du bot"""
    latency = round(bot.latency * 1000)
    
    embed = discord.Embed(
        title="🏓 Pong!",
        description=f"Latence: **{latency}ms**\n"
                   f"État: **{'✅ En ligne' if latency < 100 else '⚠️ Latence élevée'}**",
        color=discord.Color.green() if latency < 100 else discord.Color.orange()
    )
    embed.set_footer(text=f"Déployé sur Render • {datetime.now().strftime('%d/%m/%Y %H:%M')}")
    
    await interaction.response.send_message(embed=embed)

# -----------------------------
# Commandes traditionnelles (préfixe !)
# -----------------------------
@bot.command(name="aide")
async def aide_command(ctx):
    """Commande traditionnelle d'aide"""
    user_id = ctx.author.id
    has_active = user_id in conversations and conversations[user_id]["active"]
    
    message = "**🎩 Services de Lady Audrey Hall**\n\n"
    
    if has_active:
        message += f"**💬 CONVERSATION ACTIVE** dans <#{conversations[user_id]['channel_id']}>\n"
        message += "Parlez-moi normalement dans ce salon.\n"
        message += "Utilisez `/stop` pour terminer.\n\n"
    else:
        message += "**Pour converser :**\n"
        message += "`/parler [message]` - Démarrer une conversation\n\n"
    
    message += "**Mini-jeux :**\n"
    message += "`/tarot` - Tirer une carte du tarot\n"
    message += "`/devinette` - Énigme mystique\n\n"
    
    message += "**Gestion des rôles (Admin) :**\n"
    message += "`/ajouter_role [rôle]` - Ajouter un rôle\n"
    message += "`/retirer_role [rôle]` - Retirer un rôle\n"
    message += "`/roles_audrey` - Voir mes rôles\n\n"
    
    message += "**Gestion :**\n"
    message += "`/stop` - Terminer la conversation\n"
    message += "`/aide` - Afficher cette aide\n"
    message += "`/statut` - Voir le statut\n"
    message += "`/ping` - Vérifier la latence"
    
    await ctx.send(message)

@bot.command(name="stop")
async def stop_command(ctx):
    """Commande traditionnelle pour arrêter"""
    user_id = ctx.author.id
    
    if user_id in conversations and conversations[user_id]["active"]:
        conversations[user_id]["active"] = False
        await ctx.send("🕊️ Notre conversation prend fin ici. Que les mystères vous accompagnent...")
    else:
        await ctx.send("💭 Nous ne sommes pas en train de converser actuellement.")

@bot.command(name="ping")
async def ping_command(ctx):
    """Commande traditionnelle pour ping"""
    latency = round(b
