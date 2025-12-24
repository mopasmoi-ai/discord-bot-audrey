import os
import asyncio
import random
import sqlite3
import signal
import sys
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from enum import Enum
try:
    import audioop
except ImportError:
    # Créer un faux module audioop pour contourner l'erreur
    class FakeAudioop:
        def __getattr__(self, name):
            return lambda *args, **kwargs: None
    
    sys.modules['audioop'] = FakeAudioop()
    print("⚠️ Patch audioop appliqué pour Python 3.13")

# Désactiver les warnings liés à l'audio
os.environ['DISCORD_INSTALL_AUDIO_DEPS'] = '0'
import discord
from discord.ext import commands, tasks
from discord import app_commands
import google.generativeai as genai
from dotenv import load_dotenv

# ============ CONFIGURATION ============
load_dotenv()

TOKEN = os.getenv('DISCORD_TOKEN')
GEMINI_KEY = os.getenv('GEMINI_KEY')
BOT_COLOR = int(os.getenv('BOT_COLOR', '0x2E8B57'), 16)  # Vert mystérieux

intents = discord.Intents.all()
bot = commands.Bot(
    command_prefix='!',
    intents=intents,
    help_command=None,
    activity=discord.Activity(
        type=discord.ActivityType.listening,
        name="les murmures du destin"
    ),
    status=discord.Status.online
)

# ============ BASE DE DONNÉES ============
class Database:
    def __init__(self):
        self.conn = sqlite3.connect('audrey_bot.db')
        self.create_tables()
    
    def create_tables(self):
        cursor = self.conn.cursor()
        
        # Table utilisateurs
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                tarot_points INTEGER DEFAULT 0,
                last_daily TIMESTAMP,
                fortune_count INTEGER DEFAULT 0,
                mystery_level INTEGER DEFAULT 1
            )
        ''')
        
        # Table tarot readings
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS tarot_readings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                cards TEXT,
                interpretation TEXT,
                reading_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (user_id)
            )
        ''')
        
        self.conn.commit()
    
    def get_user(self, user_id: int):
        cursor = self.conn.cursor()
        cursor.execute(
            'SELECT * FROM users WHERE user_id = ?', 
            (user_id,)
        )
        row = cursor.fetchone()
        if row:
            return {
                'user_id': row[0],
                'tarot_points': row[1],
                'last_daily': row[2],
                'fortune_count': row[3],
                'mystery_level': row[4]
            }
        else:
            # Créer l'utilisateur s'il n'existe pas
            cursor.execute(
                'INSERT INTO users (user_id) VALUES (?)',
                (user_id,)
            )
            self.conn.commit()
            return {
                'user_id': user_id,
                'tarot_points': 0,
                'last_daily': None,
                'fortune_count': 0,
                'mystery_level': 1
            }
    
    def update_user(self, user_id: int, **kwargs):
        cursor = self.conn.cursor()
        set_clause = ', '.join([f'{k} = ?' for k in kwargs.keys()])
        values = list(kwargs.values()) + [user_id]
        cursor.execute(
            f'UPDATE users SET {set_clause} WHERE user_id = ?',
            values
        )
        self.conn.commit()
    
    def add_tarot_reading(self, user_id: int, cards: List[str], interpretation: str):
        cursor = self.conn.cursor()
        cursor.execute(
            'INSERT INTO tarot_readings (user_id, cards, interpretation) VALUES (?, ?, ?)',
            (user_id, ','.join(cards), interpretation)
        )
        self.conn.commit()

db = Database()

# ============ SYSTÈME DE TAROT ============
class TarotCard:
    def __init__(self, name: str, arcana: str, meaning: Dict[str, str], emoji: str):
        self.name = name
        self.arcana = arcana  # 'major' ou 'minor'
        self.upright = meaning.get('upright', '')
        self.reversed = meaning.get('reversed', '')
        self.emoji = emoji

class TarotDeck:
    def __init__(self):
        self.cards = self._create_deck()
    
    def _create_deck(self) -> List[TarotCard]:
        major_arcana = [
            TarotCard("Le Fou", "major", {
                'upright': "Nouveau départ, liberté, innocence",
                'reversed': "Imprudence, risque, folie"
            }, "🃏"),
            TarotCard("Le Mage", "major", {
                'upright': "Volonté, habileté, communication",
                'reversed': "Manipulation, tromperie"
            }, "🧙"),
            TarotCard("La Grande Prêtresse", "major", {
                'upright': "Intuition, mystère, sagesse cachée",
                'reversed': "Secrets, retrait"
            }, "🔮"),
            TarotCard("L'Impératrice", "major", {
                'upright': "Féminité, créativité, nature",
                'reversed': "Dépendance, stagnation"
            }, "👑"),
            TarotCard("L'Empereur", "major", {
                'upright': "Autorité, structure, contrôle",
                'reversed': "Tyrannie, rigidité"
            }, "🏛️"),
            TarotCard("Le Pendu", "major", {
                'upright': "Sacrifice, nouvelle perspective",
                'reversed': "Stagnation, égoïsme"
            }, "🙃"),
            TarotCard("La Mort", "major", {
                'upright': "Fin, transformation, renouveau",
                'reversed': "Peur du changement"
            }, "💀"),
            TarotCard("La Tour", "major", {
                'upright': "Destruction, révélation soudaine",
                'reversed': "Éviter l'inévitable"
            }, "⚡"),
            TarotCard("L'Étoile", "major", {
                'upright': "Espoir, inspiration, sérénité",
                'reversed': "Désespoir, manque de foi"
            }, "⭐")
        ]
        
        minor_cards = [
            TarotCard("As de Coupe", "minor", {
                'upright': "Nouvel amour, intuition",
                'reversed': "Tromperie émotionnelle"
            }, "🫖"),
            TarotCard("Dix d'Épée", "minor", {
                'upright': "Fin douloureuse, trahison",
                'reversed': "Renaissance, guérison"
            }, "⚔️"),
            TarotCard("Trois de Bâton", "minor", {
                'upright': "Expansion, vision",
                'reversed': "Obstacles, frustration"
            }, "🚢"),
            TarotCard("Reine de Pentacle", "minor", {
                'upright': "Abondance, sécurité",
                'reversed': "Matérialisme, possessivité"
            }, "💰")
        ]
        
        return major_arcana + minor_cards
    
    def draw_cards(self, num: int = 3) -> List[TarotCard]:
        return random.sample(self.cards, min(num, len(self.cards)))
    
    def get_card_reading(self, cards: List[TarotCard]) -> str:
        reading = ""
        for i, card in enumerate(cards, 1):
            orientation = random.choice(['upright', 'reversed'])
            meaning = card.upright if orientation == 'upright' else card.reversed
            reading += f"**{i}. {card.name}** {card.emoji}\n"
            reading += f"   Orientation: {'Droit' if orientation == 'upright' else 'Inversé'}\n"
            reading += f"   Signification: {meaning}\n\n"
        return reading

tarot_deck = TarotDeck()

# ============ AUDREY HALL AI ============
class AudreyHallAI:
    def __init__(self):
        genai.configure(api_key=GEMINI_KEY)
        self.model = genai.GenerativeModel(
            'gemini-2.1',
            generation_config={
                "temperature": 0.85,
                "top_p": 0.95,
                "max_output_tokens": 350
            },
            safety_settings={
                'HARM_CATEGORY_HARASSMENT': 'BLOCK_NONE',
                'HARM_CATEGORY_HATE_SPEECH': 'BLOCK_NONE',
                'HARM_CATEGORY_SEXUAL': 'BLOCK_NONE',
                'HARM_CATEGORY_DANGEROUS': 'BLOCK_NONE'
            }
        )
        self.mystery_phrases = [
            "Le Nom Interdit murmure dans les ténèbres...",
            "Les Clés de Babylone attendent leur porteur...",
            "L'Œil Qui Voit Tout observe toujours...",
            "Les Sept Lumières vacillent...",
            "Le Chemin du Fou est imprévisible..."
        ]
    
    def get_current_mystery(self) -> str:
        hour = datetime.now().hour
        mysteries = [
            (0, 6, "La Nuit des Mystères"),
            (6, 12, "L'Aube des Anciens"),
            (12, 18, "Le Jour des Révélations"),
            (18, 24, "Le Crépuscule des Secrets")
        ]
        for start, end, mystery in mysteries:
            if start <= hour < end:
                return mystery
        return "L'Heure Étrangère"
    
    def _get_audrey_signature(self) -> str:
        signatures = [
            "*sirote son thé Earl Grey avec une grâce calculée*",
            "*ajuste ses lunettes à monture dorée, un sourire énigmatique aux lèvres*",
            "*effleure les pages d'un grimoire ancien, la poussière du temps dansant dans la lumière*",
            "*laisse échapper un léger rire, aussi mystérieux que le sourire de la Joconde*",
            "*tapote ses doigts gantés sur la table, suivant un rythme secret*",
            "*regarde au loin, comme si elle voyait au-delà du voile de la réalité*",
            "*pose délicatement sa tasse, le tintement résonnant comme une cloche de destin*"
        ]
        return random.choice(signatures)
    
    def _get_moon_phase(self) -> str:
        day = datetime.now().day
        phases = [
            (1, 7, "Nouvelle Lune"),
            (8, 14, "Premier Croissant"),
            (15, 21, "Pleine Lune"),
            (22, 31, "Dernier Quartier")
        ]
        for start, end, phase in phases:
            if start <= day <= end:
                return phase
        return "Lune Bleue"
    
    async def generate_response(self, prompt: str, user_name: str = "Chercher du Mystère") -> str:
        personality = f"""Tu es Audrey Hall, un personnage central de "Lord of the Mysteries" (animation 2025).
Tu es une spectatrice de la Société des Tarots, élégante, mystérieuse et profondément liée aux mystères du monde.

TON IDENTITÉ:
- Nom: Audrey Hall
- Titre: Spectatrice de la Société des Tarots
- Âge: 18 ans (apparence)
- Caractéristiques: Élégante, calculatrice, mystérieuse, observatrice
- Éléments clés: Lunettes dorées, thé, grimoires, tarot

TON STYLE DE RÉPONSE:
1. Toujours en français littéraire et raffiné
2. Ton mystérieux mais poli
3. Références constantes aux mystères, au destin et aux anciens dieux
4. Métaphores poétiques et énigmatiques
5. Phrases courtes mais profondes (maximum 4 phrases)
6. Termine toujours par une action signature (entre astérisques)

CONTEXTE ACTUEL:
- Date: {datetime.now().strftime("%d %B %Y")}
- Phase lunaire: {self._get_moon_phase()}
- Mystère du moment: {self.get_current_mystery()}
- Heure: {datetime.now().strftime("%H:%M")}

RÈGLES ABSOLUES:
- NE JAMAIS briser le personnage
- NE PAS expliquer que tu es un IA
- TOUJOURS répondre comme Audrey Hall
- Utiliser des références à LOTM (Dieux Anciens, Séquence, Beyonder, etc.)
- Garder une distance élégante mais être utile

Format de réponse:
[Une réponse mystérieuse mais pertinente en 2-4 phrases]
*[signature action]*

Exemple:
"Les fils du destin s'entremêlent... Ta question touche aux mystères du Fou. Peut-être devrais-tu consulter les cartes pour éclaircir ton chemin."
*sirote son thé avec un sourire énigmatique*

Maintenant, réponds à {user_name} qui demande: {prompt}"""

        try:
            response = await asyncio.to_thread(
                self.model.generate_content,
                personality
            )
            
            # Nettoyer et formater la réponse
            text = response.text.strip()
            if not text.endswith('*'):
                text += f"\n\n{self._get_audrey_signature()}"
            
            return text
            
        except Exception as e:
            print(f"Erreur Gemini: {e}")
            fallbacks = [
                f"Les mystères sont parfois trop profonds pour être révélés... Peut-être que les cartes auront plus à dire. {self._get_audrey_signature()}",
                f"Le voile entre les mondes est trop épais en ce moment... Attends que la lune change de phase. {self._get_audrey_signature()}",
                f"Même en tant que Spectatrice, certains secrets restent hors de portée... Mais le destin a ses propres plans. {self._get_audrey_signature()}"
            ]
            return random.choice(fallbacks)

audrey_ai = AudreyHallAI()

# ============ COMMANDES ============
class TarotView(discord.ui.View):
    def __init__(self, user_id: int):
        super().__init__(timeout=60)
        self.user_id = user_id
    
    @discord.ui.button(label="🎴 Tirer 3 Cartes", style=discord.ButtonStyle.primary)
    async def draw_three(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        cards = tarot_deck.draw_cards(3)
        reading = tarot_deck.get_card_reading(cards)
        
        # Mettre à jour les points
        user_data = db.get_user(self.user_id)
        new_points = user_data['tarot_points'] + 5
        db.update_user(self.user_id, tarot_points=new_points)
        
        embed = discord.Embed(
            title="🔮 Tirage du Tarot - 3 Cartes",
            description=f"**Lecture pour {interaction.user.mention}**\n\n{reading}",
            color=BOT_COLOR,
            timestamp=datetime.now()
        )
        embed.add_field(name="Points Mystère", value=f"{new_points} ✨", inline=True)
        embed.add_field(name="Prochain Niveau", value=f"{new_points}/100", inline=True)
        embed.set_footer(text="Les cartes révèlent ce que le cœur sait déjà...")
        
        # Enregistrer la lecture
        db.add_tarot_reading(
            self.user_id,
            [card.name for card in cards],
            "Tirage de 3 cartes"
        )
        
        await interaction.followup.send(embed=embed)
    
    @discord.ui.button(label="🃏 Une Seule Carte", style=discord.ButtonStyle.secondary)
    async def draw_one(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        cards = tarot_deck.draw_cards(1)
        reading = tarot_deck.get_card_reading(cards)
        
        embed = discord.Embed(
            title="🎴 Carte du Jour",
            description=f"**Pour {interaction.user.mention}**\n\n{reading}",
            color=BOT_COLOR
        )
        embed.set_footer(text="Une seule carte, mais quelle signification profonde...")
        
        await interaction.followup.send(embed=embed)
    
    @discord.ui.button(label="📜 Mes Lectures", style=discord.ButtonStyle.success)
    async def my_readings(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        cursor = db.conn.cursor()
        cursor.execute(
            'SELECT cards, reading_date FROM tarot_readings WHERE user_id = ? ORDER BY reading_date DESC LIMIT 5',
            (self.user_id,)
        )
        readings = cursor.fetchall()
        
        if readings:
            description = ""
            for i, (cards, date) in enumerate(readings, 1):
                description += f"**{i}.** {cards} (*{date}*)\n"
            
            embed = discord.Embed(
                title="📜 Tes Dernières Lectures",
                description=description,
                color=BOT_COLOR
            )
        else:
            embed = discord.Embed(
                title="📜 Aucune Lecture",
                description="Les cartes n'ont pas encore parlé pour toi...\nUtilise `!tarot` pour ta première lecture.",
                color=BOT_COLOR
            )
        
        await interaction.followup.send(embed=embed)

@bot.tree.command(name="parler", description="Parler avec Audrey Hall")
@app_commands.describe(message="Ton message à Audrey")
async def parler(interaction: discord.Interaction, message: str):
    await interaction.response.defer()
    
    # Générer la réponse
    response = await audrey_ai.generate_response(message, interaction.user.name)
    
    # Créer l'embed
    embed = discord.Embed(
        title="💬 Audrey Hall murmure...",
        description=response,
        color=BOT_COLOR,
        timestamp=datetime.now()
    )
    embed.set_author(
        name="Audrey Hall - Spectatrice",
        icon_url="https://i.imgur.com/Eglj7Yt.png"  # Remplace par une vraie image si tu veux
    )
    embed.set_footer(text=f"Consultation pour {interaction.user.name}")
    
    await interaction.followup.send(embed=embed)

@bot.tree.command(name="tarot", description="Consulter les cartes du Tarot")
async def tarot(interaction: discord.Interaction):
    embed = discord.Embed(
        title="🎴 La Voix des Cartes",
        description=f"**{interaction.user.mention}**, les cartes attendent tes questions...\n\n"
                   f"Choisis comment tu souhaites consulter le tarot:",
        color=BOT_COLOR
    )
    embed.add_field(name="🎴 3 Cartes", value="Une lecture complète du passé, présent et futur", inline=False)
    embed.add_field(name="🃏 1 Carte", value="La guidance du jour", inline=False)
    embed.add_field(name="📜 Historique", value="Voir tes lectures passées", inline=False)
    embed.set_footer(text="Les cartes ne mentent jamais, mais elles parlent en énigmes...")
    
    await interaction.response.send_message(embed=embed, view=TarotView(interaction.user.id))

@bot.tree.command(name="mystere", description="Apprends ton niveau de mystère")
async def mystere(interaction: discord.Interaction):
    user_data = db.get_user(interaction.user.id)
    
    # Déterminer le titre
    levels = {
        1: "Novice des Mystères",
        2: "Apprenti du Tarot",
        3: "Chercheur de Vérité",
        4: "Gardien des Secrets",
        5: "Spectateur Élu"
    }
    level = min(user_data['mystery_level'], 5)
    title = levels.get(level, "Étranger au Mystère")
    
    # Déterminer la barre de progression
    progress = min(user_data['tarot_points'] % 100, 20)
    progress_bar = "█" * progress + "░" * (20 - progress)
    
    embed = discord.Embed(
        title=f"🔍 {title}",
        description=f"**{interaction.user.mention}**, voici ta progression dans les Mystères:",
        color=BOT_COLOR
    )
    embed.add_field(name="Niveau", value=f"**{level}**/5", inline=True)
    embed.add_field(name="Points Mystère", value=f"**{user_data['tarot_points']}** ✨", inline=True)
    embed.add_field(name="Progression", value=f"```{progress_bar}```", inline=False)
    embed.add_field(name="Lectures", value=f"**{user_data['fortune_count']}** consultations", inline=True)
    
    # Message personnalisé selon le niveau
    messages = [
        "Tu commences à peine à entrevoir les mystères...",
        "Les cartes commencent à te parler...",
        "Tu peux sentir les énergies du destin...",
        "Les secrets anciens se dévoilent à toi...",
        "Tu marches sur le chemin des Spectateurs..."
    ]
    embed.set_footer(text=messages[level-1] if level <= 5 else "Le mystère est infini...")
    
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="journal", description="Les mystères du jour")
async def journal(interaction: discord.Interaction):
    mystery = audrey_ai.get_current_mystery()
    moon = audrey_ai._get_moon_phase()
    mystery_phrase = random.choice(audrey_ai.mystery_phrases)
    
    # Générer une petite prédiction
    predictions = [
        "Un étranger pourrait entrer dans ta vie aujourd'hui...",
        "Les finances nécessitent une attention particulière...",
        "Une opportunité cachée se révèlera...",
        "Attention aux mots prononcés à la légère...",
        "Le passé refait surface, prêt à être compris..."
    ]
    
    embed = discord.Embed(
        title="📖 Journal des Mystères",
        description=f"**{datetime.now().strftime('%d %B %Y')}**\n\n"
                   f"*{mystery_phrase}*",
        color=BOT_COLOR
    )
    embed.add_field(name="🌙 Phase Lunaire", value=moon, inline=True)
    embed.add_field(name="🔮 Mystère Actif", value=mystery, inline=True)
    embed.add_field(name="💫 Prédiction du Jour", value=random.choice(predictions), inline=False)
    embed.set_footer(text="Le destin écrit, mais nous tournons les pages...")
    
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="aide", description="Toutes les commandes d'Audrey")
async def aide(interaction: discord.Interaction):
    embed = discord.Embed(
        title="📚 Guide des Mystères - Audrey Hall",
        description="Je suis Audrey Hall, Spectatrice de la Société des Tarots.\n"
                   "Voici comment interagir avec moi:",
        color=BOT_COLOR
    )
    
    embed.add_field(
        name="💬 /parler [message]",
        value="Parle-moi de tes inquiétudes, questions ou réflexions",
        inline=False
    )
    embed.add_field(
        name="🎴 /tarot",
        value="Consulte les cartes du tarot pour des conseils",
        inline=False
    )
    embed.add_field(
        name="🔍 /mystere",
        value="Découvre ton niveau dans les mystères",
        inline=False
    )
    embed.add_field(
        name="📖 /journal",
        value="Les mystères et prédictions du jour",
        inline=False
    )
    embed.add_field(
        name="🎭 /roleplay",
        value="Scène de roleplay avec Audrey",
        inline=False
    )
    
    embed.set_footer(text="Les mystères attendent ceux qui osent chercher...")
    
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="roleplay", description="Une scène de roleplay avec Audrey")
@app_commands.describe(scene="La scène que tu veux jouer")
async def roleplay(interaction: discord.Interaction, scene: str):
    scenes_db = {
        "thé": [
            "Nous prenons le thé dans mon salon... La tasse est chaude, le mystère aussi.",
            "Le thé Earl Grey dégage un parfum envoûtant... Que souhaites-tu discuter?",
            "*Verse du thé avec précision* Le thé révèle autant que les cartes, parfois..."
        ],
        "bibliothèque": [
            "Les grimoires anciens murmurent autour de nous... Quel savoir cherches-tu?",
            "La poussière des siècles recouvre ces pages... Mais la vérité brille toujours.",
            "*Ouvre un livre aux pages jaunies* Chaque ligne est un mystère à résoudre..."
        ],
        "jardin": [
            "La lune éclaire le jardin nocturne... Les fleurs ont leurs propres secrets.",
            "L'air nocturne est chargé de possibilités... Que ressens-tu ici?",
            "*Effleure une rose* Même la nature suit les lois des anciens..."
        ]
    }
    
    if scene.lower() in scenes_db:
        response = random.choice(scenes_db[scene.lower()])
    else:
        response = f"Nous nous trouvons dans un lieu incertain... {scene}? Intéressant. Que se passe-t-il ici?"
    
    embed = discord.Embed(
        title="🎭 Scène de Roleplay",
        description=f"**{interaction.user.name}** a choisi: **{scene}**\n\n"
                   f"*Audrey Hall regarde autour d'elle*\n"
                   f"{response}\n\n"
                   f"*{audrey_ai._get_audrey_signature()}*",
        color=BOT_COLOR
    )
    
    await interaction.response.send_message(embed=embed)

# ============ ÉVÉNEMENTS ============
@bot.event
async def on_ready():
    print(f'✅ {bot.user} est connecté!')
    try:
        synced = await bot.tree.sync()
        print(f'✅ {len(synced)} commandes synchronisées')
    except Exception as e:
        print(f'❌ Erreur synchronisation: {e}')

@bot.event
async def on_message(message):
    if message.author == bot.user:
        return
    
    # Réponse aléatoire aux mentions
    if bot.user.mentioned_in(message) and not message.content.startswith('!'):
        if random.random() < 0.3:  # 30% de chance de répondre
            async with message.channel.typing():
                response = await audrey_ai.generate_response(
                    f"{message.author.name} m'a mentionné en disant: {message.content}",
                    message.author.name
                )
                
                embed = discord.Embed(
                    description=response,
                    color=BOT_COLOR
                )
                await message.reply(embed=embed, mention_author=False)
    
    await bot.process_commands(message)

# ============ TÂCHES AUTOMATIQUES ============
@tasks.loop(hours=6)
async def change_mystery():
    # RECHERCHE AUTOMATIQUE D'UN CHANNEL - NE PAS MODIFIER
    for guild in bot.guilds:
        for channel in guild.text_channels:
            # Vérifie si le bot peut envoyer des messages
            if channel.permissions_for(guild.me).send_messages:
                try:
                    embed = discord.Embed(
                        title="🔄 Changement du Mystère",
                        description=f"Le mystère actif change maintenant: **{audrey_ai.get_current_mystery()}**\n\n"
                                   f"*{random.choice(audrey_ai.mystery_phrases)}*",
                        color=BOT_COLOR,
                        timestamp=datetime.now()
                    )
                    await channel.send(embed=embed)
                    print(f"✅ Message de mystère envoyé dans {channel.name}")
                    return  # Arrête après le premier envoi réussi
                except Exception as e:
                    print(f"⚠️ Impossible d'envoyer dans {channel.name}: {e}")
                    continue

@tasks.loop(hours=24)
async def daily_reset():
    print("🔄 Réinitialisation quotidienne exécutée")

# ============ LANCEMENT ============
if __name__ == "__main__":
    # Gestion des signaux
    import signal
    import sys
    
    def signal_handler(sig, frame):
        print(f'\n🔴 Signal {sig} reçu. Arrêt du bot...')
        change_mystery.cancel()
        daily_reset.cancel()
        sys.exit(0)
    
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    # Démarrer tâches après connexion
    @bot.event
    async def on_connect():
        print("✅ Connexion établie, démarrage des tâches...")
        change_mystery.start()
        daily_reset.start()
    
    # Lancer le bot
    try:
        print("🚀 Lancement du bot Audrey Hall...")
        bot.run(TOKEN)
    except KeyboardInterrupt:
        print("\n🔴 Arrêt manuel")
    except Exception as e:
        print(f"❌ Erreur: {e}")
        sys.exit(1)

from flask import Flask
from threading import Thread

# Mini serveur web pour Render
app = Flask('')

@app.route('/')
def home():
    return "✅ Audrey Hall Bot en ligne!"

def run_web_server():
    app.run(host='0.0.0.0', port=8080)

# Démarrer le serveur dans un thread séparé
web_thread = Thread(target=run_web_server, daemon=True)
web_thread.start()

# Lancer le bot (gardez votre code actuel)
if __name__ == "__main__":
    # ... votre code de lancement actuel ...
