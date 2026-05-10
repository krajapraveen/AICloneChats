"""
Seed data for Anonymous Reality Phase 1 launch.

Operator rule: small healthy rooms > large chaotic rooms.
Empty rooms kill anonymous products faster than bad features.

Each room ships with 4-7 starter messages from synthetic anonymous handles
to break the empty-room failure mode. Handles are clearly marked as `is_seed=True`
in DB so they cannot be impersonated and are visually distinct.
"""

ROOMS = [
    {
        "slug": "loneliness",
        "title": "Loneliness",
        "description": "For when the room is loud but you feel quiet. No fixing required. Just being heard.",
        "rules": [
            "Speak honestly. No performative sadness.",
            "No giving unsolicited advice unless asked.",
            "No DMing anyone. Stay in the room.",
        ],
    },
    {
        "slug": "family-pressure",
        "title": "Family Pressure",
        "description": "Expectations, comparisons, weight you didn't choose to carry. Talk about what's actually heavy.",
        "rules": [
            "No naming family members.",
            "No revenge fantasies — talk about the feeling, not the strategy.",
            "Vent is fine. Hate is not.",
        ],
    },
    {
        "slug": "money-reality",
        "title": "Money Reality",
        "description": "What money is actually doing to your life — pressure, shame, fear, control, freedom.",
        "rules": [
            "No flexing. No specific salary numbers.",
            "No hustle culture spam.",
            "Talk about the feeling, not the formula.",
        ],
    },
    {
        "slug": "mental-load",
        "title": "Mental Load",
        "description": "The invisible to-do list that runs in your head all day. The things only you remember.",
        "rules": [
            "No comparison Olympics.",
            "Specific is better than general.",
            "No optimizing other people's mental load for them.",
        ],
    },
    {
        "slug": "relationships",
        "title": "Relationships",
        "description": "Friendships, partnerships, family. The honest version, not the curated one.",
        "rules": [
            "No naming the other person.",
            "No revenge advice.",
            "Strong opinions about ideas; never about strangers' partners.",
        ],
    },
    {
        "slug": "startup-struggle",
        "title": "Startup Struggle",
        "description": "Founder loneliness, runway fear, identity-fused work. Honest, not performative.",
        "rules": [
            "No company names, no fundraise flexing.",
            "No 'rise and grind' platitudes.",
            "Talk about what's actually hard, not what's photogenic.",
        ],
    },
    {
        "slug": "student-life",
        "title": "Student Life",
        "description": "Exam pressure, future uncertainty, the gap between who you are and who you're supposed to become.",
        "rules": [
            "No naming professors, schools, or classmates.",
            "No academic dishonesty discussions.",
            "Be kind. Everyone here is figuring it out.",
        ],
    },
    {
        "slug": "general-reality",
        "title": "General Reality",
        "description": "Everything else. The thing you've been carrying that doesn't fit a label.",
        "rules": [
            "No promotion, no marketing.",
            "No politics-as-team-sport.",
            "Speak from your life, not from a hot take.",
        ],
    },
]


SEED_CONVERSATIONS = {
    "loneliness": [
        ("QuietRiver28", "i went the entire weekend without speaking out loud. didn't realize until i tried to order coffee monday morning and my voice cracked."),
        ("HonestMoon17", "the worst part is i HAVE friends. i just don't have the energy to be the version of myself they expect."),
        ("PaperKite94", "anyone else feel lonelier in group chats than alone?"),
        ("SilentFox92", "yes. group chats made me feel more invisible than empty rooms ever did."),
        ("OakWind33", "started leaving voice notes to myself in my phone just to hear someone talking back. it actually helped a bit."),
    ],
    "family-pressure": [
        ("StoneRiver41", "spent an hour today rehearsing what i'd say if my mom asks why i haven't called more. didn't end up calling."),
        ("WildSparrow12", "my parents 'don't expect' anything from me. that turned out to be more pressure, not less."),
        ("CloudHorse55", "at what point do you stop trying to make them proud and just try to make yourself unembarrassed?"),
        ("RuggedFern07", "i'm 34 and i still hide my plates after eating something my mom would've called unhealthy."),
    ],
    "money-reality": [
        ("WhisperPine22", "i make 'good money' on paper. i still get a stress headache before checking my bank account."),
        ("BrightCanyon66", "growing up broke didn't go away when i wasn't broke anymore. i still feel like i'm pretending."),
        ("FogElm88", "weirdest part — i feel less safe now that i have savings. like waiting for it to disappear."),
        ("MorningTide19", "my partner doesn't understand why i can't just buy the thing. it's not the thing. it's the feeling of buying the thing."),
    ],
    "mental-load": [
        ("AshFinch73", "i remember the dog's vet appointment, the kid's permission slip, and which of my friends is going through what — and somehow i'm the one who's 'not contributing enough'."),
        ("CalmPebble29", "i fell asleep last night still mentally rearranging my whole monday."),
        ("HollowMaple50", "the worst is when someone says 'just tell me what to do.' if i have to do the telling i've already done half the work."),
        ("SoftRiver11", "i've started keeping a list of things i thought of so the brain noise has somewhere to go."),
    ],
    "relationships": [
        ("MistFox64", "i love them but i don't recognize myself in this relationship anymore."),
        ("DimEmber82", "had a friendship for 10 years. realized last month i've been performing it the whole time."),
        ("PatientGale07", "is it possible to love someone deeply and also need a long break from them?"),
        ("HeavyDawn36", "best advice i ever got: 'date the person you'd want to be sad with.' changed everything."),
    ],
    "startup-struggle": [
        ("LeanFalcon99", "told everyone things are great. they're not great. they're just not catastrophic."),
        ("MossLake14", "raised money. felt worse, not better. now i owe other people my dream."),
        ("CrowFog37", "every founder i know is some mix of exhausted, scared, and pretending. nobody wants to be the one who admits it first."),
        ("SaltStorm44", "took my first weekend off in 8 months last week. realized my identity was 90% the company. that's the actual problem."),
    ],
    "student-life": [
        ("OliveDawn18", "i'm 19 and i feel like the rest of my life is being decided by who i was at 17."),
        ("LinenStorm46", "everyone keeps asking 'what are you going to do?' as if i picked a major from a menu of fates."),
        ("RowanGust55", "the assignments aren't the hard part. it's the feeling of being constantly evaluated as a person."),
        ("PineWisp23", "i used to love this subject. now i can't tell if i love it or i'm just good at it."),
    ],
    "general-reality": [
        ("DriftHaven71", "weird week. nothing terrible happened. nothing good happened. just the feeling of time passing without me."),
        ("KindCanyon90", "my therapist asked what i wanted out of my 30s. i didn't have an answer. that scared me more than anything has in years."),
        ("CharlockSky08", "i think i'm okay. i think 'i'm okay' might also be the problem."),
        ("MeadowFlint62", "saw a stranger crying on the train today. didn't say anything. wish i had."),
    ],
}
