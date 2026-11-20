# Αυτόματες ανακοινώσεις σχολών στο Instagram

Κάθε ώρα διαβάζει το RSS κάθε τμήματος και ανεβάζει τις νέες ανακοινώσεις ως post
(εικόνα + λεζάντα με link) στον λογαριασμό Instagram του αντίστοιχου συλλόγου.
Τρέχει δωρεάν στο GitHub Actions και δεν χρειάζεται server.

## Στήσιμο (μία φορά)

### 1. Repo στο GitHub
1. Φτιάξε **δημόσιο** repo (π.χ. `uni-announcer`) και ανέβασε όλα τα αρχεία.
   Πρέπει να είναι δημόσιο, γιατί το Instagram κατεβάζει τις εικόνες από το
   `raw.githubusercontent.com`. Τα tokens μένουν κρυφά στα Secrets.
2. **Settings → Actions → General → Workflow permissions → Read and write**.

### 2. Λογαριασμοί Instagram (για κάθε σύλλογο)
- Στην εφαρμογή Instagram: **Ρυθμίσεις → Τύπος λογαριασμού → Επαγγελματικός**
  (Creator ή Business).
- Σελίδα Facebook **δεν** χρειάζεται με αυτόν τον τρόπο σύνδεσης.

### 3. Εφαρμογή Meta και tokens
1. Στο https://developers.facebook.com → **Create App** → τύπος *Business*.
2. Πρόσθεσε το προϊόν **Instagram** → **API setup with Instagram login**.
3. **App roles → Roles → Instagram Testers**: πρόσθεσε τους 13 λογαριασμούς.
   Από κάθε λογαριασμό αποδέξου την πρόσκληση (Instagram → Ρυθμίσεις →
   Εφαρμογές και ιστότοποι → Προσκλήσεις testers).
   Όσο οι λογαριασμοί είναι testers, δεν χρειάζεται App Review.
4. Στο ίδιο σημείο του dashboard: **Add account** για κάθε λογαριασμό →
   **Generate token**. Τα δικαιώματα που χρειάζονται είναι
   `instagram_business_basic` και `instagram_business_content_publish`.

### 4. Secrets στο GitHub (Settings → Secrets and variables → Actions)
- **`IG_TOKENS`**: ένα JSON με το `id` του config και το token του καθενός:
  ```json
  {"mie": "IGAAx...", "civil": "IGAAy...", "arch": "IGAAz..."}
  ```
- **`GH_PAT`**: fine-grained token από το GitHub (Settings → Developer settings →
  Personal access tokens), μόνο για αυτό το repo, με δικαίωμα
  **Secrets: Read and write**. Το χρειάζεται η αυτόματη ανανέωση των tokens,
  που αλλιώς λήγουν σε 60 μέρες.

### 5. Σύλλογοι
Άνοιξε το `config.yaml` και πρόσθεσε ένα block για κάθε σύλλογο.
Τα περισσότερα site τμημάτων είναι WordPress, οπότε το RSS είναι `https://site/?feed=rss2`.
Αν αυτό είναι άδειο, δοκίμασε `https://site/news/feed/` (έτσι είναι στο econ και στο civ).
Για site **χωρίς RSS** (π.χ. το arch) βάλε `type: html` και `url:` τη σελίδα
ανακοινώσεων. Το script βρίσκει μόνο του τίτλο και ημερομηνία σε κάθε ανακοίνωση.
Αν κάποτε αλλάξει η εμφάνιση του site και δεν βρει τίποτα, η εκτέλεση αποτυγχάνει
και το GitHub σου στέλνει email.

**Μετά την πρώτη εκτέλεση** άνοιξε το log στο Actions: για κάθε σύλλογο
γράφει τις 3 πιο πρόσφατες ανακοινώσεις που βρήκε. Έτσι βλέπεις αμέσως αν
διαβάζει σωστά κάθε site.

### 6. Πρώτη εκτέλεση
**Actions → Έλεγχος ανακοινώσεων → Run workflow.**
Στην πρώτη εκτέλεση οι ήδη υπάρχουσες ανακοινώσεις σημειώνονται ως «γνωστές» και
**δεν** ανεβαίνουν, ώστε να μη γεμίσει ο λογαριασμός με παλιά posts. Από εκεί και
πέρα ανεβαίνουν μόνο οι καινούριες. Αν θέλεις να ανέβουν και μερικές πρόσφατες,
άλλαξε το `first_run_posts` στο config.

## Χρήσιμα
- **Δοκιμή χωρίς Instagram:** `python announcer.py --dry-run`. Φτιάχνει μόνο τις εικόνες στο `images/`.
- **Ένας σύλλογος μόνο:** `python announcer.py --only mie`
- **Αν κάτι αποτύχει,** ξαναδοκιμάζει την επόμενη ώρα, έως 3 φορές.
  Το log φαίνεται στο Actions και το GitHub σου στέλνει email όταν μια εκτέλεση αποτύχει.
- **Όριο:** 100 posts ανά λογαριασμό το 24ωρο. Με `max_posts_per_run: 4` δεν το πλησιάζεις.
- **Η ώρα στο GitHub Actions δεν είναι ακριβής:** μια εκτέλεση μπορεί να καθυστερήσει 5–20 λεπτά.
