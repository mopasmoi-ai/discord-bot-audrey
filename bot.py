import os
import asyncio
import random
import sqlite3
import signal
import sys
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
import json

# Désactiver les warnings liés à l'audio
os.environ['DISCORD_INSTALL_AUDIO_DEPS'] = '0'
import discord
from discord.ext import commands, tasks
from discord import app_commands
import aiohttp
from dotenv import load_dotenv

# ============ CONFIGURATION ============
load_dotenv()

# VALIDATION DES VARIABLES D'ENVIRONNEMENT (CORRECTION AJOUTÉE)
TOKEN = os.getenv('DISCORD_TOKEN')
DEEPSEEK_API_KEY = os.getenv('DEEPSEEK_API_KEY')
BOT_COLOR = int(os.getenv('BOT_COLOR', '2E8B57'), 16)  # Vert mystérieux

# Validation cruciale pour éviter les crashs silencieux
print("=" * 50)
print("🔧 INITIALISATION DU BOT AUDREY HALL")
print("=" * 50)

if not DEEPSEEK_API_KEY:
    print("❌ ERREUR: DEEPSEEK_API_KEY est vide ou non définie!")
    print(f"   Valeur actuelle: '{DEEPSEEK_API_KEY}'")
    print("   ⚠️ Le bot continuera mais les réponses IA seront limitées")
else:
    print(f"✅ Clé API DeepSeek chargée (longueur: {len(DEEPSEEK_API_KEY)} chars)")
    print(f"   Préfixe: {DEEPSEEK_API_KEY[:10]}...")

if not TOKEN:
    print("❌ ERREUR FATALE: DISCORD_TOKEN est vide!")
    sys.exit(1)
else:
    print("✅ Token Discord chargé")

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
            }, "⭐"),
            TarotCard("La Lune", "major", {
                'upright': "Illusion, intuition, subconscient",
                'reversed': "Confusion, peur"
            }, "🌙"),
            TarotCard("Le Soleil", "major", {
                'upright': "Joie, succès, vitalité",
                'reversed': "Tristesse temporaire"
            }, "☀️"),
            TarotCard("Le Jugement", "major", {
                'upright': "Renaissance, absolution",
                'reversed': "Doute, autocritique"
            }, "⚖️")
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
            }, "💰"),
            TarotCard("Chevalier de Coupe", "minor", {
                'upright': "Romance, invitation",
                'reversed': "Déception, jalousie"
            }, "🏇"),
            TarotCard("Cinq de Pentacle", "minor", {
                'upright': "Perte, pauvreté",
                'reversed': "Rétablissement"
            }, "🏚️"),
            TarotCard("Deux d'Épée", "minor", {
                'upright': "Choix difficile, équilibre",
                'reversed': "Indécision, confusion"
            }, "⚔️⚔️")
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

# ============ DEEPSEEK API CLIENT (AMÉLIORÉ) ============
class DeepSeekClient:
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.base_url = "https://api.deepseek.com/v1/chat/completions"
        self.headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
        print(f"🔧 Client DeepSeek initialisé (URL: {self.base_url})")
        
    async def generate_response(self, messages: List[Dict], max_tokens: int = 800) -> str:
        """Envoie une requête à l'API DeepSeek avec logging détaillé"""
        if not self.api_key or self.api_key == "votre_cle_api_deepseek_ici":
            print("❌ API KEY DeepSeek invalide ou manquante!")
            return None
        
        try:
            payload = {
                "model": "deepseek-chat",
                "messages": messages,
                "max_tokens": max_tokens,
                "temperature": 0.85,
                "top_p": 0.9,
                "frequency_penalty": 0.2,
                "presence_penalty": 0.1,
                "stream": False
            }
            
            print(f"\n📡 Envoi requête à DeepSeek...")
            print(f"   Premier message: {messages[0]['content'][:80]}...")
            print(f"   Prompt utilisateur: {messages[1]['content'][:80]}...")
            
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    self.base_url,
                    headers=self.headers,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=30)
                ) as response:
                    
                    print(f"📥 Réponse reçue - Status: {response.status}")
                    
                    if response.status == 200:
                        data = await response.json()
                        print(f"✅ Réponse API valide reçue")
                        return data['choices'][0]['message']['content']
                    elif response.status == 401:
                        error_text = await response.text()
                        print(f"❌ ERREUR 401: Authentification échouée!")
                        print(f"   Vérifiez votre clé API DeepSeek")
                        print(f"   Réponse: {error_text[:200]}")
                        return None
                    elif response.status == 429:
                        print(f"⚠️ ERREUR 429: Trop de requêtes (rate limit)")
                        return None
                    elif response.status == 400:
                        error_text = await response.text()
                        print(f"❌ ERREUR 400: Mauvaise requête")
                        print(f"   Détail: {error_text[:200]}")
                        return None
                    else:
                        error_text = await response.text()
                        print(f"❌ ERREUR {response.status}: {error_text[:200]}")
                        return None
                        
        except asyncio.TimeoutError:
            print("⏱️ Timeout: La requête a pris plus de 30 secondes")
            return None
        except aiohttp.ClientError as e:
            print(f"🌐 Erreur réseau: {type(e).__name__}: {e}")
            return None
        except Exception as e:
            print(f"💥 Exception inattendue: {type(e).__name__}: {e}")
            return None

# ============ AUDREY HALL AI (AMÉLIORÉE) ============
class AudreyHallAI:
    def __init__(self):
        if not DEEPSEEK_API_KEY or DEEPSEEK_API_KEY == "votre_cle_api_deepseek_ici":
            print("⚠️ Attention: Clé API DeepSeek manquante ou non configurée")
            print("   Audrey utilisera des réponses prédéfinies uniquement")
            self.deepseek = None
        else:
            self.deepseek = DeepSeekClient(DEEPSEEK_API_KEY)
            
        self.mystery_phrases = [
            "Le Nom Interdit murmure dans les ténèbres...",
            "Les Clés de Babylone attendent leur porteur...",
            "L'Œil Qui Voit Tout observe toujours...",
            "Les Sept Lumières vacillent...",
            "Le Chemin du Fou est imprévisible...",
            "Les Séquences s'entremêlent...",
            "Les potions Beyonder bouillonnent...",
            "Les rituels anciens appellent..."
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
            "*pose délicatement sa tasse, le tintement résonnant comme une cloche de destin*",
            "*touche délicatement son pendentif en argent, sentant les énergies mystiques*"
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
    
    async def generate_response(self, prompt: str, user_name: str = "Chercheur du Mystère") -> str:
        print(f"\n🎭 Audrey génère une réponse pour: {user_name}")
        print(f"📝 Prompt: {prompt}")
        
        # Si pas d'API DeepSeek, utiliser des réponses intelligentes prédéfinies
        if not self.deepseek:
            print("⚠️ Mode hors-ligne: utilisation de réponses prédéfinies")
            responses = [
                f"*réfléchit un moment* Ta question sur '{prompt[:30]}...' est intéressante. Les cartes pourraient en dire plus sur ce sujet. {self._get_audrey_signature()}",
                f"*sirote son thé* Tu t'interroges sur '{prompt[:30]}...'. Le destin révèle ses secrets à ceux qui savent observer. {self._get_audrey_signature()}",
                f"*regarde ses cartes* '{prompt[:30]}...' Hmm. La réponse se cache dans les ombres, mais persévère. {self._get_audrey_signature()}"
            ]
            return random.choice(responses)
        
        # Version SIMPLIFIÉE du prompt pour meilleurs résultats
        system_prompt = f"""Tu es Audrey Hall de "Lord of the Mysteries". Tu es une Spectatrice de la Société des Tarots.
Tu es mystérieuse, élégante et profonde. Réponds à la question de manière pertinente et utile, en restant dans ton personnage.

Date: {datetime.now().strftime("%d %B %Y")}
Phase lunaire: {self._get_moon_phase()}
Mystère du moment: {self.get_current_mystery()}

Règles importantes:
1. Réponds TOUJOURS en tant qu'Audrey Hall
2. Sois mystérieuse mais utile
3. Réponds directement à la question posée
4. Termine par une action entre *astérisques*

Question de {user_name}: {prompt}

Réponse d'Audrey Hall:"""

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt}
        ]
        
        try:
            response = await self.deepseek.generate_response(messages, max_tokens=500)
            
            if response:
                print(f"✅ Réponse DeepSeek reçue ({len(response)} chars)")
                print(f"   Prévisualisation: {response[:100]}...")
                
                text = response.strip()
                
                # Nettoyage basique
                text = text.replace("En tant qu'IA, ", "En tant que Spectatrice, ")
                text = text.replace("En tant qu'IA ", "En tant qu'Audrey Hall ")
                
                # Ajouter signature si absente
                if not text.endswith('*') and not '*' in text[-50:]:
                    signature = self._get_audrey_signature()
                    text += f"\n\n{signature}"
                    print(f"   Signature ajoutée: {signature}")
                
                # Limiter la longueur
                if len(text) > 1500:
                    text = text[:1400] + "..."
                    if not text.endswith('*'):
                        text += f"\n\n{self._get_audrey_signature()}"
                
                return text
            else:
                print("❌ Réponse vide de l'API - utilisation de fallback intelligent")
                # Fallback intelligent qui utilise le contexte de la question
                fallbacks = [
                    f"*réfléchit intensément* Ta question sur '{prompt[:40]}...' touche à des mystères profonds. Peut-être devrions-nous consulter les cartes pour plus de clarté. {self._get_audrey_signature()}",
                    f"*effleure son pendentif* '{prompt[:40]}...' Les énergies sont troubles aujourd'hui. Reviens me voir quand la lune sera pleine. {self._get_audrey_signature()}",
                    f"*regarde au loin* Ton interrogation sur '{prompt[:40]}...' mérite réflexion. La Société des Tarots étudie ces mystères. {self._get_audrey_signature()}"
                ]
                return random.choice(fallbacks)
                
        except Exception as e:
            print(f"💥 Exception dans generate_response: {type(e).__name__}: {e}")
            return f"Les énergies mystiques sont perturbées... Reviens plus tard. {self._get_audrey_signature()}"

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
                description="Les cartes n'ont pas encore parlé pour toi...\nUtilise `/tarot` pour ta première lecture.",
                color=BOT_COLOR
            )
        
        await interaction.followup.send(embed=embed)

@bot.tree.command(name="parler", description="Parler avec Audrey Hall")
@app_commands.describe(message="Ton message à Audrey")
async def parler(interaction: discord.Interaction, message: str):
    await interaction.response.defer()
    
    print(f"\n💬 Commande /parler de {interaction.user.name}")
    print(f"   Message: {message}")
    
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
        name="Audrey Hall - Spectatrice de la Société des Tarots",
        icon_url="https://i.imgur.com/Eglj7Yt.png"
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
        "Le passé refait surface, prêt à être compris...",
        "Un message mystérieux pourrait t'être destiné...",
        "Les énergies divinatoires sont fortes aujourd'hui..."
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
        name="💬 `/parler [message]`",
        value="Parle-moi de tes inquiétudes, questions ou réflexions",
        inline=False
    )
    embed.add_field(
        name="🎴 `/tarot`",
        value="Consulte les cartes du tarot pour des conseils",
        inline=False
    )
    embed.add_field(
        name="🔍 `/mystere`",
        value="Découvre ton niveau dans les mystères",
        inline=False
    )
    embed.add_field(
        name="📖 `/journal`",
        value="Les mystères et prédictions du jour",
        inline=False
    )
    embed.add_field(
        name="🎭 `/roleplay [scène]`",
        value="Scène de roleplay avec Audrey",
        inline=False
    )
    
    embed.set_footer(text="Les mystères attendent ceux qui osent chercher...")
    
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="roleplay", description="Une scène de roleplay avec Audrey")
@app_commands.describe(scene="La scène que tu veux jouer (thé, bibliothèque, jardin, salon)")
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
        ],
        "salon": [
            "Le salon de la Société des Tarots est silencieux ce soir... Les énergies mystiques sont palpables.",
            "Les rideaux de velour rouge vibrent légèrement... Comme s'ils réagissaient aux présences invisibles.",
            "*S'assoit dans un fauteuil en cuir* Ici, nous sommes protégés des regards indiscrets..."
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
    print(f'📊 Serviteurs: {len(bot.guilds)}')
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
                # Extraire le message sans la mention
                content = message.content.replace(f'<@{bot.user.id}>', '').strip()
                if content:
                    print(f"👂 Mention de {message.author.name}: {content}")
                    response = await audrey_ai.generate_response(
                        f"{message.author.name} m'a mentionné en disant: {content}",
                        message.author.name
                    )
                    
                    embed = discord.Embed(
                        description=response,
                        color=BOT_COLOR
                    )
                    await message.reply(embed=embed, mention_author=False)

# ============ TÂCHES AUTOMATIQUES ============
@tasks.loop(hours=6)
