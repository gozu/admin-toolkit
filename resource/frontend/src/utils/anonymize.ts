// Hidden display-anonymization ("screenshot") mode.
//
// Typing the keyword in the shell (see AppShell) flips a localStorage flag and
// reloads. While on, every identifying string the app has seen in data —
// people, projects, connections, code envs, hosts, clusters, groups, emails —
// is rewritten to a stable fictional alias (characters, codenames and places
// drawn from all over fiction; the org itself renders as Acme Corp/acme.com)
// at the DOM level: a TreeWalker pass plus a MutationObserver over
// document.body, with explicit hooks for canvas chart labels and the report
// export, which a DOM pass can't reach.
//
// Aliases never contain digits (counters overflow into adjective compounds,
// not numeric suffixes), and "acme" appears only in the org name and domain,
// never inside connection/env/object names. Scenario names are deliberately
// NOT anonymized, and generic UI vocabulary (module labels, common DSS terms)
// is stoplisted so a customer object named e.g. "Overview" can never cause the
// app's own chrome to be rewritten.
//
// Display-only by design: API calls, hrefs and DSS-bound exports carry real
// values, and CSV table exports inherit the aliases because they scrape the
// rendered DOM. The alias dictionary persists to localStorage so the same real
// entity keeps the same alias across pages, reloads and screenshots. Known
// residual leaks (accepted): text typed into inputs/textareas (e.g. the raw-log
// analysis textarea), raw file downloads, and free-text strings naming entities
// the app never loaded as data.

import { getRegisteredScanStores, type RegisteredScanStore } from '../state/scanStoreRegistry';

const MODE_KEY = 'admin-toolkit:screenshotMode';
// v2: bumped when the alias scheme changed (numberless, no acme-prefixed
// names, UI-word stoplist) so dictionaries minted by the old scheme are
// discarded rather than kept alive by persistence.
const DICT_KEY = 'admin-toolkit:screenshotDict2';
const LEGACY_DICT_KEY = 'admin-toolkit:screenshotDict';

type EntityClass =
  | 'person' | 'email' | 'group' | 'org' | 'project' | 'object' | 'connection'
  | 'codeenv' | 'llm' | 'hostlabel' | 'hosturl' | 'nodeid' | 'installid'
  | 'cluster' | 'namespace' | 'registry' | 'cloudid' | 'ip';

// ── Alias pools ──────────────────────────────────────────────────────────────
// Display names only — logins (first-initial + last name, falling back to
// first.last) and emails (first.last@acme.com) derive from them. When the cast
// runs out, new people are minted from the first-name × last-name grid, which
// gives tens of thousands of digit-free combinations.

const CAST: readonly string[] = [
  // Looney Tunes
  'Bugs Bunny', 'Daffy Duck', 'Porky Pig', 'Elmer Fudd', 'Wile Coyote',
  'Yosemite Sam', 'Tweety Bird', 'Foghorn Leghorn', 'Marvin Martian',
  'Speedy Gonzales', 'Lola Bunny',
  // Hanna-Barbera
  'Fred Flintstone', 'Wilma Flintstone', 'Barney Rubble', 'Betty Rubble',
  'Pebbles Flintstone', 'George Jetson', 'Jane Jetson', 'Judy Jetson',
  'Elroy Jetson', 'Scooby Doo', 'Shaggy Rogers', 'Velma Dinkley',
  'Daphne Blake', 'Fred Jones', 'Yogi Bear', 'Huckleberry Hound',
  // Disney / Pixar
  'Mickey Mouse', 'Minnie Mouse', 'Donald Duck', 'Daisy Duck',
  'Scrooge McDuck', 'Launchpad McQuack', 'Darkwing Duck', 'Buzz Lightyear',
  'Woody Pride', 'Bob Parr', 'Helen Parr', 'Edna Mode', 'Judy Hopps',
  'Nick Wilde',
  // More toons
  'Tom Cat', 'Jerry Mouse', 'Rocky Squirrel', 'Bullwinkle Moose',
  'Boris Badenov', 'Natasha Fatale', 'Johnny Bravo', 'Dipper Pines',
  'Mabel Pines', 'Kim Possible', 'Ron Stoppable', 'Danny Fenton',
  'April Oneil', 'Casey Jones', 'Arnold Shortman', 'Helga Pataki',
  'Doug Funnie', 'Patti Mayonnaise', 'Steven Universe', 'Finn Mertens',
  // Peanuts
  'Charlie Brown', 'Sally Brown', 'Lucy Van Pelt', 'Linus Van Pelt',
  'Peppermint Patty',
  // Simpsons / Futurama
  'Homer Simpson', 'Marge Simpson', 'Bart Simpson', 'Lisa Simpson',
  'Maggie Simpson', 'Ned Flanders', 'Montgomery Burns', 'Waylon Smithers',
  'Moe Szyslak', 'Seymour Skinner', 'Edna Krabappel', 'Nelson Muntz',
  'Ralph Wiggum', 'Troy McClure', 'Philip Fry', 'Turanga Leela',
  'Bender Rodriguez', 'Hubert Farnsworth', 'Amy Wong', 'Hermes Conrad',
  'John Zoidberg', 'Zapp Brannigan', 'Rick Sanchez', 'Morty Smith',
  'Summer Smith',
  // SpongeBob
  'Spongebob Squarepants', 'Patrick Star', 'Squidward Tentacles',
  'Sandy Cheeks', 'Eugene Krabs', 'Sheldon Plankton',
  // Star Wars
  'Luke Skywalker', 'Leia Organa', 'Han Solo', 'Lando Calrissian',
  'Obiwan Kenobi', 'Padme Amidala', 'Poe Dameron', 'Rey Skywalker',
  'Mace Windu', 'Ahsoka Tano', 'Din Djarin', 'Jyn Erso', 'Cassian Andor',
  // Star Trek
  'James Kirk', 'Leonard McCoy', 'Nyota Uhura', 'Montgomery Scott',
  'Hikaru Sulu', 'Pavel Chekov', 'Jean Luc Picard', 'William Riker',
  'Beverly Crusher', 'Deanna Troi', 'Benjamin Sisko', 'Kira Nerys',
  'Kathryn Janeway',
  // Tolkien
  'Frodo Baggins', 'Bilbo Baggins', 'Samwise Gamgee', 'Peregrin Took',
  'Meriadoc Brandybuck', 'Rosie Cotton',
  // Harry Potter
  'Harry Potter', 'Hermione Granger', 'Ron Weasley', 'Ginny Weasley',
  'Albus Dumbledore', 'Minerva McGonagall', 'Severus Snape', 'Luna Lovegood',
  'Neville Longbottom', 'Draco Malfoy', 'Sirius Black', 'Remus Lupin',
  'Rubeus Hagrid', 'Nymphadora Tonks',
  // Marvel
  'Peter Parker', 'Tony Stark', 'Steve Rogers', 'Bruce Banner',
  'Natasha Romanoff', 'Wanda Maximoff', 'Stephen Strange', 'Carol Danvers',
  'Scott Lang', 'Matt Murdock', 'Jessica Jones', 'Luke Cage', 'Peter Quill',
  'Nick Fury', 'Pepper Potts', 'Miles Morales', 'Gwen Stacy', 'Reed Richards',
  'Susan Storm', 'Ben Grimm', 'Charles Xavier', 'Jean Grey', 'Ororo Munroe',
  // DC
  'Clark Kent', 'Lois Lane', 'Jimmy Olsen', 'Bruce Wayne',
  'Alfred Pennyworth', 'Dick Grayson', 'Barbara Gordon', 'Selina Kyle',
  'Diana Prince', 'Barry Allen', 'Hal Jordan', 'Arthur Curry', 'Oliver Queen',
  // Game of Thrones
  'Jon Snow', 'Arya Stark', 'Sansa Stark', 'Eddard Stark', 'Tyrion Lannister',
  'Cersei Lannister', 'Jaime Lannister', 'Daenerys Targaryen',
  'Brienne Tarth', 'Samwell Tarly', 'Petyr Baelish', 'Jorah Mormont',
  'Davos Seaworth', 'Margaery Tyrell',
  // Dune / Foundation / Hitchhiker's / Discworld
  'Paul Atreides', 'Leto Atreides', 'Duncan Idaho', 'Gurney Halleck',
  'Liet Kynes', 'Hari Seldon', 'Arthur Dent', 'Ford Prefect',
  'Zaphod Beeblebrox', 'Tricia McMillan', 'Sam Vimes', 'Havelock Vetinari',
  'Esme Weatherwax', 'Gytha Ogg', 'Moist Lipwig', 'Tiffany Aching',
  // Sherlock
  'Sherlock Holmes', 'John Watson', 'Mycroft Holmes', 'Irene Adler',
  'James Moriarty',
  // Classic literature
  'Elizabeth Bennet', 'Fitzwilliam Darcy', 'Jane Eyre', 'Edward Rochester',
  'Jay Gatsby', 'Nick Carraway', 'Daisy Buchanan', 'Atticus Finch',
  'Scout Finch', 'Holden Caulfield', 'Ebenezer Scrooge', 'Oliver Twist',
  'David Copperfield', 'Philip Pirrip', 'Phileas Fogg', 'Jean Valjean',
  'Edmond Dantes', 'Victor Frankenstein', 'Mina Harker', 'Jonathan Harker',
  'Jim Hawkins', 'Tom Sawyer', 'Huckleberry Finn', 'Anne Shirley',
  // Children's & YA books
  'Gilbert Blythe', 'Jo March', 'Amy March', 'Beth March', 'Mary Poppins',
  'Wendy Darling', 'Dorothy Gale', 'Willy Wonka', 'Charlie Bucket',
  'Veruca Salt', 'Matilda Wormwood', 'Pippi Longstocking',
  'Katniss Everdeen', 'Peeta Mellark', 'Percy Jackson', 'Annabeth Chase',
  'Lyra Belacqua', 'Will Parry', 'Lee Scoresby', 'Artemis Fowl',
  'Holly Short', 'Ender Wiggin', 'Susan Pevensie', 'Edmund Pevensie',
  'Lucy Pevensie',
  // Film
  'Peter Venkman', 'Egon Spengler', 'Ray Stantz', 'Winston Zeddemore',
  'Marty McFly', 'Emmett Brown', 'Ellen Ripley', 'Sarah Connor',
  'John McClane', 'Alan Grant', 'Ellie Sattler', 'Ian Malcolm',
  'Inigo Montoya', 'Jack Sparrow', 'Elizabeth Swann', 'Indiana Jones',
  'Marion Ravenwood', 'James Bond', 'Forrest Gump', 'Ferris Bueller',
  'Elle Woods', 'Rocky Balboa', 'Apollo Creed', 'Napoleon Dynamite',
  // Firefly / Battlestar
  'Malcolm Reynolds', 'Zoe Washburne', 'Hoban Washburne', 'Kaylee Frye',
  'Inara Serra', 'Jayne Cobb', 'River Tam', 'Simon Tam', 'Kara Thrace',
  'William Adama', 'Gaius Baltar', 'Laura Roslin',
  // Doctor Who / Buffy / X-Files
  'Rose Tyler', 'Amy Pond', 'Rory Williams', 'Clara Oswald', 'River Song',
  'Donna Noble', 'Jack Harkness', 'Buffy Summers', 'Willow Rosenberg',
  'Xander Harris', 'Rupert Giles', 'Cordelia Chase', 'Fox Mulder',
  'Dana Scully',
  // Stranger Things
  'Mike Wheeler', 'Nancy Wheeler', 'Dustin Henderson', 'Lucas Sinclair',
  'Will Byers', 'Joyce Byers', 'Jim Hopper', 'Steve Harrington',
  'Robin Buckley', 'Eddie Munson',
  // Breaking Bad
  'Walter White', 'Jesse Pinkman', 'Skyler White', 'Hank Schrader',
  'Saul Goodman', 'Gus Fring', 'Kim Wexler',
  // The Office / Parks & Rec
  'Michael Scott', 'Dwight Schrute', 'Jim Halpert', 'Pam Beesly',
  'Andy Bernard', 'Kevin Malone', 'Angela Martin', 'Oscar Martinez',
  'Stanley Hudson', 'Creed Bratton', 'Kelly Kapoor', 'Erin Hannon',
  'Leslie Knope', 'Ron Swanson', 'Tom Haverford', 'April Ludgate',
  'Andy Dwyer', 'Ben Wyatt', 'Ann Perkins', 'Donna Meagle',
  // Sitcoms
  'Liz Lemon', 'Jack Donaghy', 'Tracy Jordan', 'Jenna Maroney',
  'Jake Peralta', 'Amy Santiago', 'Rosa Diaz', 'Terry Jeffords',
  'Raymond Holt', 'Charles Boyle', 'Gina Linetti', 'Jeff Winger',
  'Britta Perry', 'Abed Nadir', 'Troy Barnes', 'Annie Edison',
  'Shirley Bennett', 'Maurice Moss', 'Roy Trenneman', 'Jen Barber',
  'Douglas Reynholm', 'Richard Hendricks', 'Erlich Bachman',
  'Dinesh Chugtai', 'Bertram Gilfoyle', 'Jared Dunn', 'Monica Hall',
  'Gavin Belson', 'Ted Lasso', 'Rebecca Welton', 'Roy Kent', 'Keeley Jones',
  'Jamie Tartt', 'Nathan Shelley', 'George Costanza', 'Elaine Benes',
  'Cosmo Kramer', 'Chandler Bing', 'Monica Geller', 'Ross Geller',
  'Rachel Green', 'Phoebe Buffay', 'Joey Tribbiani', 'Sam Malone',
  'Diane Chambers', 'Rebecca Howe', 'Norm Peterson', 'Cliff Clavin',
  'Frasier Crane', 'Daphne Moon', 'Dorothy Zbornak', 'Blanche Devereaux',
  'Rose Nylund', 'Sophia Petrillo', 'Johnny Rose', 'Moira Rose',
  'David Rose', 'Alexis Rose', 'Stevie Budd',
  // Drama
  'Don Draper', 'Peggy Olson', 'Roger Sterling', 'Joan Holloway',
  'Pete Campbell', 'Betty Draper', 'Jed Bartlet', 'Josh Lyman',
  'Toby Ziegler', 'Sam Seaborn', 'Leo McGarry', 'Gregory House',
  'James Wilson', 'Lisa Cuddy', 'Robert Chase', 'Eric Foreman',
  'John Dorian', 'Elliot Reid', 'Perry Cox', 'Benjamin Pierce',
  'Margaret Houlihan', 'Maxwell Klinger',
  // Games
  'Gordon Freeman', 'Alyx Vance', 'Barney Calhoun', 'Ash Ketchum',
  'Gary Oak', 'Garrus Vakarian',
  // Anime & Ghibli
  'Naruto Uzumaki', 'Sasuke Uchiha', 'Sakura Haruno', 'Kakashi Hatake',
  'Hinata Hyuga', 'Edward Elric', 'Alphonse Elric', 'Winry Rockbell',
  'Roy Mustang', 'Riza Hawkeye', 'Spike Spiegel', 'Faye Valentine',
  'Shinji Ikari', 'Rei Ayanami', 'Misato Katsuragi', 'Usagi Tsukino',
  'Light Yagami', 'Sophie Hatter', 'Howl Pendragon', 'Chihiro Ogino',
  // Addams Family
  'Gomez Addams', 'Morticia Addams', 'Wednesday Addams', 'Pugsley Addams',
];

// First × last grid for people beyond the cast (cross-universe mashups —
// "Frodo Skywalker" — are a feature, not a bug).
const FIRSTS: string[] = [];
const LASTS: string[] = [];
{
  const fs = new Set<string>();
  const ls = new Set<string>();
  for (const full of CAST) {
    const parts = full.split(' ');
    fs.add(parts[0]);
    ls.add(parts[parts.length - 1]);
  }
  FIRSTS.push(...fs);
  LASTS.push(...ls);
}

// Adjectives prepended when a pool wraps — the overflow story everywhere, so
// no alias ever carries a numeric suffix.
const ADJ = [
  'crimson', 'midnight', 'turbo', 'cosmic', 'emerald', 'thunder', 'shadow',
  'golden', 'iron', 'neon', 'arctic', 'blazing', 'quantum', 'mystic',
  'scarlet', 'obsidian', 'silver', 'wild', 'lucky', 'rogue', 'stellar',
  'atomic', 'velvet', 'copper', 'phantom', 'royal', 'electric', 'amber',
];

// Project codenames (bare — never "Project X", never numbered).
const CODENAMES = [
  'kraken', 'moonshot', 'jackalope', 'tumbleweed', 'sasquatch', 'stardust',
  'thunderclap', 'marshmallow', 'porcupine', 'zeppelin', 'flamingo',
  'avocado', 'blizzard', 'cactus', 'dynamite', 'eclipse', 'firefly',
  'gumdrop', 'hullabaloo', 'iceberg', 'jukebox', 'kazoo', 'lighthouse',
  'mongoose', 'narwhal', 'octopus', 'pinball', 'quicksand', 'roadtrip',
  'submarine', 'tornado', 'ukulele', 'volcano', 'waffles', 'xylophone',
  'yeti', 'zigzag', 'bumblebee', 'cannonball', 'doodlebug', 'fizzbang',
  'gizmo', 'honeypot', 'igloo', 'jalopy', 'kumquat', 'llama', 'mermaid',
  'nimbus', 'outback', 'pelican', 'quasar', 'rickshaw', 'snorkel', 'tadpole',
  'whirlwind', 'banjo', 'catapult', 'dirigible', 'griffin', 'pegasus',
  'phoenix', 'basilisk', 'chimera', 'hydra', 'minotaur', 'centaur', 'golem',
  'kelpie', 'wyvern', 'banshee', 'valkyrie', 'leviathan', 'manticore',
  'sphinx', 'thunderbird', 'wendigo', 'zephyr', 'avalon', 'camelot',
  'xanadu', 'valhalla', 'olympus',
];

// DSS objects (datasets, recipes, models…) — fictional artifacts.
const OBJECT_WORDS = [
  'mithril', 'kryptonite', 'vibranium', 'adamantium', 'unobtainium',
  'dilithium', 'mjolnir', 'lightsaber', 'palantir', 'horcrux', 'excalibur',
  'batmobile', 'tricorder', 'delorean', 'hoverboard', 'flubber', 'portkey',
  'holocron', 'kyber', 'beskar', 'allspark', 'energon', 'dragonglass',
  'stormbreaker', 'elderwand', 'narsil', 'glamdring', 'anduril', 'silmaril',
  'arkenstone', 'tesseract', 'bifrost', 'gungnir', 'neuralyzer', 'ectoplasm',
  'babel_fish', 'flux_capacitor', 'proton_pack', 'red_pill', 'infinity_stone',
];

// Connections — fictional places (a data warehouse named gringotts).
const PLACES = [
  'gringotts', 'rivendell', 'hogsmeade', 'winterfell', 'gotham', 'metropolis',
  'wakanda', 'asgard', 'tatooine', 'dagobah', 'endor', 'naboo', 'coruscant',
  'krypton', 'vulcan', 'atlantis', 'eldorado', 'wonderland', 'neverland',
  'narnia', 'mordor', 'moria', 'isengard', 'fangorn', 'lothlorien', 'erebor',
  'braavos', 'valyria', 'meereen', 'kamino', 'mustafar', 'alderaan', 'hoth',
  'jakku', 'arrakis', 'caladan', 'trantor', 'terminus', 'gallifrey',
  'vormir', 'knowhere', 'xandar', 'sakaar', 'latveria', 'genosha',
  'themyscira',
];

// Code envs — fictional potions & substances (suffixed _env).
const ENV_WORDS = [
  'polyjuice', 'veritaserum', 'amortentia', 'wolfsbane', 'mandrake',
  'bezoar', 'gillyweed', 'pixiedust', 'senzu', 'melange', 'soylent',
  'midichlorian', 'bacta', 'carbonite', 'slurm', 'lembas', 'miruvor',
  'butterbeer', 'firewhisky', 'skooma', 'elixir', 'mana', 'aether',
  'orichalcum', 'phazon', 'tiberium', 'vespene', 'positronic',
];

// Groups — fictional crews and factions.
const CREWS = [
  'toon-squad', 'scooby-gang', 'looney-tunes', 'flintstones', 'jetsons',
  'powerpuffs', 'rugrats', 'animaniacs', 'thundercats', 'gummi-bears',
  'ducktales', 'care-bears', 'smurfs', 'snorks', 'wacky-racers',
  'herculoids', 'fellowship', 'avengers', 'ghostbusters', 'goonies',
  'starfleet', 'jedi-order', 'gryffindor', 'hufflepuff', 'ravenclaw',
  'slytherin', 'night-watch', 'browncoats', 'rebel-alliance', 'planeteers',
  'power-rangers', 'teen-titans', 'justice-league', 'autobots',
  'sailor-scouts', 'straw-hats', 'mystery-inc',
];

// LLMs — fictional AIs.
const AI_NAMES = [
  'hal', 'jarvis', 'glados', 'skynet', 'tars', 'kitt', 'marvin', 'ultron',
  'friday', 'edith', 'samantha', 'baymax', 'gerty', 'wintermute', 'multivac',
  'shodan', 'holly', 'deep-thought',
];

// K8s clusters — fictional ships.
const SHIPS = [
  'serenity', 'nostromo', 'rocinante', 'galactica', 'normandy', 'bebop',
  'swordfish', 'sulaco', 'icarus', 'axiom', 'event-horizon', 'heart-of-gold',
  'red-dwarf', 'planet-express', 'milano', 'benatar', 'waverider',
  'andromeda', 'prometheus', 'hyperion',
];

// K8s namespaces — fictional towns.
const TOWNS = [
  'springfield', 'bedrock', 'quahog', 'hill-valley', 'sunnydale', 'hawkins',
  'derry', 'gravity-falls', 'bikini-bottom', 'toontown', 'duckburg',
  'emerald-city', 'smallville', 'riverdale', 'twin-peaks', 'arkham', 'shire',
  'brigadoon', 'stars-hollow', 'pawnee', 'mos-eisley', 'godrics-hollow',
];

// Hosts, node ids and cloud/install ids — NATO words.
const NATO = [
  'alpha', 'bravo', 'charlie', 'delta', 'echo', 'foxtrot', 'golf', 'hotel',
  'india', 'juliet', 'kilo', 'lima', 'mike', 'november', 'oscar', 'papa',
  'quebec', 'romeo', 'sierra', 'tango', 'uniform', 'victor', 'whiskey',
  'xray', 'yankee', 'zulu',
];

const AWS_REGIONS = [
  'us-east-1', 'us-west-2', 'eu-west-1', 'eu-central-1', 'ap-southeast-1',
  'ca-central-1',
];

// Values never treated as identifying, per class.
const PERSON_STOP = new Set([
  'admin', 'root', 'user', 'users', 'dataiku', 'system', 'api', 'test',
  'guest', 'nobody', 'daemon', 'postgres', 'ubuntu', 'ec2-user', 'centos',
  'unknown', 'none',
]);
const GROUP_STOP = new Set([
  'users', 'admins', 'administrators', 'everyone', 'guest', 'guests',
  'public', 'default', 'readers', 'writers', 'all',
]);
const HOST_STOP = new Set(['local', 'local dss']);

// Never identifying, for ANY class: the app's own chrome (nav sections,
// module labels) and generic DSS/UI vocabulary. Registering one of these —
// because some customer dashboard or dataset happens to carry the name —
// would make the global matcher rewrite the app's own UI text.
const GENERIC_STOP = new Set([
  // nav sections + module labels (utils/moduleRegistry.ts)
  'overview', 'agents', 'connections', 'projects', 'users', 'plugins',
  'code envs', 'ai compute', 'misc', 'mission control', 'summary',
  'filesystem', 'resources', 'inventory', 'insights', 'health',
  'fs migration', 'project cleaner', 'app instances', 'scenarios', 'compute',
  'cost / cru', 'activity', 'churn & seats', 'installed', 'plugin sync',
  'cleaner', 'comparison', 'broken', 'container execs', 'docker images',
  'replace cs template', 'model audit', 'k8s insights', 'agent tuning',
  'agent permissions', 'how agents work', 'settings', 'errors',
  'sanity check', 'db health', 'report', 'feedback',
  // generic vocabulary
  'agent', 'project', 'scenario', 'user', 'connection', 'dataset',
  'datasets', 'recipe', 'recipes', 'model', 'models', 'dashboard',
  'dashboards', 'notebook', 'notebooks', 'wiki', 'insight', 'job', 'jobs',
  'log', 'logs', 'home', 'default', 'main', 'admin', 'administrator', 'test',
  'data', 'general', 'global', 'local', 'shared', 'none', 'unknown', 'total',
  'other', 'all', 'error', 'warning', 'success', 'failed', 'active',
  'inactive', 'design', 'automation', 'deployer', 'govern', 'api', 'python',
  'spark', 'sql', 'managed', 'builtin', 'built-in', 'filesystem_managed',
  'filesystem_folders', 'filesystem_root',
]);

// ── Mode flag ────────────────────────────────────────────────────────────────

function readEnabled(): boolean {
  try {
    return globalThis.localStorage?.getItem(MODE_KEY) === '1';
  } catch {
    return false;
  }
}

// Read once — flipping the flag always goes through a reload, so a stale value
// can't be observed within a page lifetime.
const enabled = readEnabled();

export function isAnonEnabled(): boolean {
  return enabled;
}

/** Keyword handler: flip the flag and reload — the DOM rewriter only ever
 *  starts (or stays off) from a clean boot, so both directions reload. */
export function toggleAnonMode(): void {
  try {
    globalThis.localStorage?.setItem(MODE_KEY, enabled ? '0' : '1');
  } catch {
    return;
  }
  window.location.reload();
}

// ── Dictionary (real → alias), persisted for cross-reload stability ──────────

let dict: Record<string, string> = {};
let counters: Partial<Record<EntityClass, number>> = {};
const aliasValues = new Set<string>();
let dictVersion = 0;
let matcher: RegExp | null = null;
let matcherVersion = -1;

function loadDict(): void {
  try {
    globalThis.localStorage?.removeItem(LEGACY_DICT_KEY);
    const raw = globalThis.localStorage?.getItem(DICT_KEY);
    if (!raw) return;
    const parsed = JSON.parse(raw) as { dict?: Record<string, string>; counters?: Partial<Record<EntityClass, number>> };
    if (parsed && typeof parsed.dict === 'object') {
      dict = parsed.dict ?? {};
      counters = parsed.counters ?? {};
      for (const v of Object.values(dict)) aliasValues.add(v);
      dictVersion++;
    }
  } catch {
    /* corrupt or unavailable — start clean */
  }
}

let persistTimer: ReturnType<typeof setTimeout> | null = null;
function schedulePersist(): void {
  if (persistTimer) return;
  persistTimer = setTimeout(() => {
    persistTimer = null;
    try {
      globalThis.localStorage?.setItem(DICT_KEY, JSON.stringify({ dict, counters }));
    } catch {
      /* quota / unavailable */
    }
  }, 500);
}

function next(cls: EntityClass): number {
  const n = (counters[cls] ?? 0) + 1;
  counters[cls] = n;
  return n;
}

/** Pool pick that overflows into adjective compounds ("crimson_kraken"),
 *  never numeric suffixes. */
function poolAlias(pool: readonly string[], n: number, sep: string): string {
  const i = n - 1;
  let name = pool[i % pool.length];
  let cycle = Math.floor(i / pool.length);
  while (cycle > 0) {
    name = ADJ[(cycle - 1) % ADJ.length] + sep + name;
    cycle = Math.floor((cycle - 1) / ADJ.length);
  }
  return name;
}

function titleWords(s: string, sep: string): string {
  return s
    .split(sep)
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(' ');
}

function emailFor(display: string): string {
  return `${display.toLowerCase().replace(/[^a-z ]/g, '').trim().replace(/ +/g, '.')}@acme.com`;
}

function loginFor(display: string): string {
  const parts = display.toLowerCase().replace(/[^a-z ]/g, '').trim().split(/ +/);
  if (parts.length < 2) return parts[0] ?? 'toon';
  const short = parts[0].charAt(0) + parts[parts.length - 1];
  return aliasValues.has(short) ? parts.join('.') : short;
}

/** Next unused fictional person. All three forms (display, login, email) are
 *  reserved together so no two people can ever share any of them. */
function nextCharacter(): { display: string; login: string } {
  for (let tries = 0; tries < 5000; tries++) {
    const i = next('person') - 1;
    let display: string;
    if (i < CAST.length) {
      display = CAST[i];
    } else {
      const k = i - CAST.length;
      const f = k % FIRSTS.length;
      const d = Math.floor(k / FIRSTS.length);
      display = `${FIRSTS[f]} ${LASTS[(f + d) % LASTS.length]}`;
    }
    const login = loginFor(display);
    const mail = emailFor(display);
    if (!aliasValues.has(display) && !aliasValues.has(login) && !aliasValues.has(mail)) {
      aliasValues.add(display);
      aliasValues.add(login);
      aliasValues.add(mail);
      return { display, login };
    }
  }
  return { display: CAST[0], login: loginFor(CAST[0]) };
}

const NODE_SUFFIX_RE = /-(design|automation|api|deployer|govern)$/;
const KEYISH_RE = /^[A-Z0-9_]+$/;

function mint(value: string, cls: EntityClass): string {
  switch (cls) {
    case 'person': {
      const c = nextCharacter();
      return /\s/.test(value) ? c.display : c.login;
    }
    case 'email': {
      const c = nextCharacter();
      return emailFor(c.display);
    }
    case 'group': return poolAlias(CREWS, next('group'), '-');
    case 'org': return 'Acme Corp';
    case 'project': {
      const code = poolAlias(CODENAMES, next('project'), '_');
      return KEYISH_RE.test(value) ? code.toUpperCase() : titleWords(code, '_');
    }
    case 'object': {
      const w = poolAlias(OBJECT_WORDS, next('object'), '_');
      return KEYISH_RE.test(value) ? w.toUpperCase() : w;
    }
    case 'connection': return poolAlias(PLACES, next('connection'), '_');
    case 'codeenv': return `${poolAlias(ENV_WORDS, next('codeenv'), '_')}_env`;
    case 'llm': {
      const w = poolAlias(AI_NAMES, next('llm'), '-');
      return /[A-Z\s]/.test(value) ? titleWords(w, '-') : w;
    }
    case 'hostlabel': return `DSS ${titleWords(poolAlias(NATO, next('hostlabel'), '-'), '-')}`;
    case 'hosturl': return `https://dss-${poolAlias(NATO, next('hosturl'), '-')}.acme.com`;
    case 'nodeid': {
      const m = value.match(NODE_SUFFIX_RE);
      const base = poolAlias(NATO, next('nodeid'), '-');
      return m ? `${base}-${m[1]}` : `dss-${base}`;
    }
    case 'installid': return `install-${poolAlias(NATO, next('installid'), '-')}`;
    case 'cluster': return poolAlias(SHIPS, next('cluster'), '-');
    case 'namespace': return poolAlias(TOWNS, next('namespace'), '-');
    case 'registry': return `123456789012.dkr.ecr.${AWS_REGIONS[(next('registry') - 1) % AWS_REGIONS.length]}.amazonaws.com`;
    case 'cloudid': return `cloud-${poolAlias(NATO, next('cloudid'), '-')}`;
    case 'ip': {
      const n = next('ip');
      return `10.42.${Math.floor(n / 256) % 256}.${n % 256}`;
    }
  }
}

// Classes whose alias may legitimately repeat (a fixed brand / a small
// realistic pool) — everything else re-mints on collision.
const DUP_OK = new Set<EntityClass>(['org', 'registry', 'person', 'email']);

function register(rawValue: unknown, cls: EntityClass): void {
  if (typeof rawValue !== 'string') return;
  let value = rawValue.trim();
  if (cls === 'person' && value.startsWith('dssuser_')) value = value.slice('dssuser_'.length);
  if (value.length < 3 || value.length > 160) return;
  if (/^\d+$/.test(value)) return;
  if (dict[value] !== undefined || aliasValues.has(value)) return;
  const lower = value.toLowerCase();
  if (GENERIC_STOP.has(lower)) return;
  if (cls === 'person' && PERSON_STOP.has(lower)) return;
  if (cls === 'group' && GROUP_STOP.has(lower)) return;
  if (cls === 'hostlabel' && HOST_STOP.has(lower)) return;
  let alias = mint(value, cls);
  for (let tries = 0; aliasValues.has(alias) && !DUP_OK.has(cls) && tries < 50; tries++) {
    alias = mint(value, cls);
  }
  dict[value] = alias;
  aliasValues.add(alias);
  // Lowercase twin for uppercase project keys: K8s pod names and container
  // labels carry them lowercased. Length-gated to avoid eating common words.
  if (cls === 'project' && /^[A-Z0-9_]{5,}$/.test(value) && !/^\d/.test(value)) {
    const lc = value.toLowerCase();
    if (dict[lc] === undefined && !aliasValues.has(lc) && !GENERIC_STOP.has(lc)) {
      dict[lc] = alias.toLowerCase();
      aliasValues.add(alias.toLowerCase());
    }
  }
  dictVersion++;
}

// ── Entity collection: a field-name-driven walk over any data payload ────────

const FIELD_MAP: Record<string, EntityClass> = {
  login: 'person', owner: 'person', ownerLogin: 'person', ownerDisplayName: 'person',
  displayName: 'person', authIdentifier: 'person', lastModifiedBy: 'person',
  runAsUser: 'person', lastEditor: 'person', dssSubmitter: 'person',
  submitter: 'person', createdBy: 'person', author: 'person', authors: 'person',
  instanceOwners: 'person', user: 'person',
  email: 'email', ownerEmail: 'email', userEmail: 'email', triage_recipient: 'email',
  triageRecipient: 'email',
  groups: 'group', group: 'group',
  company: 'org',
  projectKey: 'project', projectKeys: 'project', originProjectKey: 'project',
  creatorProjectKey: 'project', targetProjectKey: 'project',
  referencingProjects: 'project', projectKeyForSend: 'project', projectName: 'project',
  datasetName: 'object', recipeName: 'object', creatorRecipeName: 'object',
  objectName: 'object', assetName: 'object', notebookName: 'object',
  appId: 'object',
  connection: 'connection', connectionName: 'connection',
  codeEnvName: 'codeenv', codeEnvNames: 'codeenv', envName: 'codeenv',
  sourceEnvName: 'codeenv', targetEnvName: 'codeenv', codeEnv: 'codeenv',
  codeEnvKeys: 'codeenv',
  llmId: 'llm', friendlyName: 'llm',
  kubernetesNamespace: 'namespace', namespace: 'namespace', ns: 'namespace',
  clusterId: 'cluster', clusterName: 'cluster', kubeCtlContext: 'cluster',
  currentContext: 'cluster',
  registryUrl: 'registry', repositoryURL: 'registry',
  server: 'hosturl', instanceUrl: 'hosturl', url: 'hosturl',
  nodeId: 'nodeid', installId: 'installid',
  vpcId: 'cloudid', subnetIds: 'cloudid', securityGroups: 'cloudid',
};

interface UserLike { login?: unknown; displayName?: unknown; email?: unknown }

/** Users seed as linked triples so one character owns login + name + email. */
function seedUser(u: UserLike): void {
  const login = typeof u.login === 'string' ? u.login.trim() : '';
  const loginLower = login.toLowerCase();
  if (!login || login.length < 3 || PERSON_STOP.has(loginLower) || GENERIC_STOP.has(loginLower)) return;
  if (dict[login] === undefined && !aliasValues.has(login)) {
    const c = nextCharacter();
    dict[login] = c.login;
    aliasValues.add(c.login);
    dictVersion++;
    const display = typeof u.displayName === 'string' ? u.displayName.trim() : '';
    if (display.length >= 3 && dict[display] === undefined && !aliasValues.has(display)) {
      dict[display] = c.display;
      aliasValues.add(c.display);
    }
    const email = typeof u.email === 'string' ? u.email.trim() : '';
    if (email.length >= 3 && dict[email] === undefined && !aliasValues.has(email)) {
      dict[email] = emailFor(c.display);
      aliasValues.add(emailFor(c.display));
    }
  }
}

function walkForEntities(v: unknown, depth: number): void {
  if (v == null || depth > 12) return;
  if (Array.isArray(v)) {
    for (const x of v) walkForEntities(x, depth + 1);
    return;
  }
  if (typeof v !== 'object') return;
  const o = v as Record<string, unknown>;
  for (const [k, val] of Object.entries(o)) {
    if (k === 'users' && Array.isArray(val)) {
      for (const u of val) if (u && typeof u === 'object') seedUser(u as UserLike);
      // fall through to the generic walk for groups etc.
    }
    if (k === 'codeEnvSizes' && val && typeof val === 'object' && !Array.isArray(val)) {
      for (const envName of Object.keys(val)) register(envName, 'codeenv');
      continue;
    }
    if (k === 'connectionDetails' || k === 'connectionHealth') {
      if (Array.isArray(val)) {
        for (const c of val) {
          const name = (c as { name?: unknown } | null)?.name;
          register(name, 'connection');
        }
      }
      continue;
    }
    if (k === 'clusters' && Array.isArray(val)) {
      for (const c of val) register((c as { name?: unknown } | null)?.name, 'cluster');
      // fall through: server/vpcId etc. picked up by the generic walk
    }
    const cls = FIELD_MAP[k]
      // Object names ({name/label, projectKey} rows) — but never scenario rows
      // (scenario names stay real by user decision; their projectKey /
      // runAsUser / lastModifiedBy still alias via FIELD_MAP).
      ?? ((k === 'name' || k === 'label')
        && ('projectKey' in o || 'projectName' in o)
        && !('scenarioType' in o) && !('scenarioId' in o)
        ? 'object'
        : undefined)
      // Host records ({id, label, url}): the label and the short host id both
      // render (host cards, feedback context, audit host column). `id` is
      // required so plain {label, url} link rows never register.
      ?? ((k === 'label' || k === 'id') && 'id' in o && 'url' in o && 'label' in o
        ? 'hostlabel'
        : undefined);
    if (typeof val === 'string') {
      if (cls) register(val, cls);
    } else if (Array.isArray(val) && cls && val.every((x) => typeof x === 'string')) {
      for (const s of val) register(s, cls);
    } else {
      walkForEntities(val, depth + 1);
    }
  }
}

/** Feed any data payload (API response, parsedData, scan-store data) into the
 *  dictionary. No-op when the mode is off. */
export function anonCollect(value: unknown): void {
  if (!enabled || value == null) return;
  const before = dictVersion;
  try {
    walkForEntities(value, 0);
  } catch {
    /* never let collection break a data path */
  }
  if (dictVersion !== before) {
    schedulePersist();
    scheduleFullPass();
  }
}

// ── Text rewriting ───────────────────────────────────────────────────────────

const RE_ESCAPE = /[.*+?^${}()|[\]\\]/g;
const EMAIL_RE = /[\w.+-]+@[\w-]+(?:\.[\w-]+)+/g;
const IPV4_RE = /\b(?:\d{1,3}\.){3}\d{1,3}\b/g;
const ECR_ACCOUNT_RE = /\b\d{12}(?=\.dkr\.ecr)/g;

function getMatcher(): RegExp | null {
  if (matcherVersion === dictVersion) return matcher;
  matcherVersion = dictVersion;
  const terms = Object.keys(dict).sort((a, b) => b.length - a.length);
  matcher = terms.length
    ? new RegExp(
        `(?<![A-Za-z0-9])(?:${terms.map((t) => t.replace(RE_ESCAPE, '\\$&')).join('|')})(?![A-Za-z0-9])`,
        'g',
      )
    : null;
  return matcher;
}

/** Alias every known real entity inside `input`, then catch stray emails, IPs
 *  and ECR account ids. Identity function while the mode is off — safe to call
 *  unconditionally from chart label callbacks. */
export function anonText(input: string): string {
  if (!enabled || !input) return input;
  let out = input;
  const m = getMatcher();
  if (m) out = out.replace(m, (hit) => dict[hit] ?? hit);
  out = out.replace(EMAIL_RE, (e) => {
    if (e.endsWith('@acme.com')) return e;
    const known = dict[e];
    if (known) return known;
    register(e, 'email');
    return dict[e.trim()] ?? e;
  });
  out = out.replace(IPV4_RE, (ip) => {
    if (ip.startsWith('10.42.') || ip.startsWith('127.') || ip === '0.0.0.0') return ip;
    const known = dict[ip];
    if (known) return known;
    register(ip, 'ip');
    return dict[ip] ?? ip;
  });
  out = out.replace(ECR_ACCOUNT_RE, '123456789012');
  return out;
}

// ── DOM rewriter ─────────────────────────────────────────────────────────────

const SKIP_TAGS = new Set(['SCRIPT', 'STYLE', 'NOSCRIPT', 'IFRAME', 'TEXTAREA', 'INPUT']);
const REWRITE_ATTRS = ['title', 'aria-label', 'alt', 'placeholder'];

let observer: MutationObserver | null = null;
let passTimer: ReturnType<typeof setTimeout> | null = null;

// SSE-fed scan stores never pass through fetchJson — harvest their data via the
// registry instead. Idempotent and re-checked on every mutation batch so
// lazily-registered stores are picked up too.
const subscribedStores = new WeakSet<RegisteredScanStore>();
function ensureScanSubscriptions(): void {
  for (const entry of getRegisteredScanStores()) {
    if (subscribedStores.has(entry)) continue;
    subscribedStores.add(entry);
    let t: ReturnType<typeof setTimeout> | null = null;
    entry.subscribe(() => {
      if (t) return;
      t = setTimeout(() => {
        t = null;
        anonCollect(entry.rawData?.());
      }, 400);
    });
  }
}

function rewriteTextNode(node: Text): void {
  const parent = node.parentElement;
  if (!parent) return;
  // Leave the subtree the user is actively editing alone (report slides are
  // contentEditable) — unfocused editable content still gets rewritten.
  if (parent.isContentEditable && document.activeElement?.contains(parent)) return;
  const value = node.nodeValue;
  if (!value) return;
  const replaced = anonText(value);
  if (replaced !== value) node.nodeValue = replaced;
}

function rewriteElementAttrs(el: Element): void {
  for (const attr of REWRITE_ATTRS) {
    const value = el.getAttribute(attr);
    if (!value) continue;
    const replaced = anonText(value);
    if (replaced !== value) el.setAttribute(attr, replaced);
  }
}

function applyToTree(root: Node): void {
  if (root.nodeType === Node.TEXT_NODE) {
    rewriteTextNode(root as Text);
    return;
  }
  if (root.nodeType !== Node.ELEMENT_NODE && root.nodeType !== Node.DOCUMENT_NODE) return;
  if (root.nodeType === Node.ELEMENT_NODE) {
    if (SKIP_TAGS.has((root as Element).tagName)) return;
    rewriteElementAttrs(root as Element);
  }
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT | NodeFilter.SHOW_TEXT, {
    acceptNode: (node) =>
      node.nodeType === Node.ELEMENT_NODE && SKIP_TAGS.has((node as Element).tagName)
        ? NodeFilter.FILTER_REJECT
        : NodeFilter.FILTER_ACCEPT,
  });
  let node = walker.nextNode();
  while (node) {
    if (node.nodeType === Node.TEXT_NODE) rewriteTextNode(node as Text);
    else rewriteElementAttrs(node as Element);
    node = walker.nextNode();
  }
}

function scheduleFullPass(): void {
  if (!observer || passTimer) return;
  passTimer = setTimeout(() => {
    passTimer = null;
    applyToTree(document.body);
    observer?.takeRecords();
  }, 250);
}

/** Boot the DOM rewriter (main.tsx). No-op while the mode is off. */
export function initAnonMode(): void {
  if (!enabled || observer || typeof document === 'undefined') return;
  loadDict();
  observer = new MutationObserver((records) => {
    ensureScanSubscriptions();
    for (const r of records) {
      if (r.type === 'characterData' && r.target.nodeType === Node.TEXT_NODE) {
        rewriteTextNode(r.target as Text);
      } else if (r.type === 'attributes' && r.target.nodeType === Node.ELEMENT_NODE) {
        rewriteElementAttrs(r.target as Element);
      } else if (r.type === 'childList') {
        r.addedNodes.forEach((n) => applyToTree(n));
      }
    }
    // Discard the records our own writes just queued — everything above ran
    // synchronously, so nothing external can be interleaved with them.
    observer?.takeRecords();
  });
  observer.observe(document.body, {
    subtree: true,
    childList: true,
    characterData: true,
    attributes: true,
    attributeFilter: REWRITE_ATTRS,
  });
  ensureScanSubscriptions();
  applyToTree(document.body);
  observer.takeRecords();
}
