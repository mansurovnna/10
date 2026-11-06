let LOAN_DAYS = 14;
let FINE_PER_DAY = 50;
const COVER_COLORS = ["#718a74", "#b68762", "#7d8198", "#668b92", "#b27a70", "#a48d62", "#72889f", "#879274"];
const AVATAR_COLORS = ["#e8eee7", "#f0e9df", "#e7edf1", "#efe8ed", "#e7eee9"];

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

function dateOffset(days) {
  const date = new Date();
  date.setDate(date.getDate() + days);
  return date.toISOString();
}

function makeId(prefix) {
  return `${prefix}-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 7)}`;
}

let library = null;
let adminUsername = "";
let databaseVersion = 0;
let currentSettings = { loan_days: LOAN_DAYS, fine_per_day: FINE_PER_DAY };
let pendingAdminRequests = 0;

function markAdminRequestPending(change) {
  pendingAdminRequests = Math.max(0, pendingAdminRequests + change);
  const logoutButton = $("#logoutButton");
  if (logoutButton) logoutButton.disabled = pendingAdminRequests > 0;
}

class Book {
  constructor({ id, title, author, year, genre = "", description = "", cover = "" }) {
    this.id = id || makeId("b");
    this.title = title.trim();
    this.author = author.trim();
    this.year = Number(year);
    this.genre = genre.trim();
    this.description = description.trim();
    this.cover = cover;
  }

  DisplayInfo() {
    return `${this.title} — ${this.author} (${this.year})`;
  }

  SetTitle(title) {
    this.title = title.trim();
  }
}

class Reader {
  constructor({ id, firstName, lastName = "", phone = "" }) {
    this.id = id || makeId("r");
    this.firstName = firstName.trim();
    this.lastName = lastName.trim();
    this.phone = phone.trim();
  }

  get name() {
    return `${this.firstName} ${this.lastName}`.trim();
  }
}

class Library {
  static TotalBooks = 0;
  static libraryName = "Читальня";

  constructor(data) {
    this.books = data.books.map((book) => new Book(book));
    this.readers = data.readers.map((reader) => new Reader(reader));
    this.loans = data.loans;
    this.version = data.version || 1;
    this.persistQueue = Promise.resolve();
    Library.TotalBooks = this.books.length;
  }

  AddBook(bookData) {
    const book = new Book(bookData);
    this.books.push(book);
    Library.TotalBooks += 1;
    this.save();
    return book;
  }

  GetBookCount() {
    return this.books.length;
  }

  IsBookAvailable(bookId) {
    return !this.loans.some((loan) => loan.bookId === bookId);
  }

  CalculateBookAge(year) {
    return Math.max(0, new Date().getFullYear() - Number(year));
  }

  FindBook(title) {
    const normalized = title.trim().toLocaleLowerCase("ru");
    return this.books.find((book) => book.title.toLocaleLowerCase("ru") === normalized);
  }

  IsReaderRegistered(name) {
    const normalized = name.trim().toLocaleLowerCase("ru");
    return this.readers.some((reader) => reader.name.toLocaleLowerCase("ru") === normalized);
  }

  static ShowLibraryName() {
    return Library.libraryName;
  }

  ShowBooks() {
    return this.books;
  }

  static CalculateFine(overdueDays) {
    return Math.max(0, overdueDays) * FINE_PER_DAY;
  }

  static GetMaxBooks(first, second) {
    return Math.max(first, second);
  }

  GetAvailableBooks() {
    return this.books.filter((book) => this.IsBookAvailable(book.id));
  }

  SearchBook(value, searchBy = "title") {
    const normalized = String(value).trim().toLocaleLowerCase("ru");
    return this.books.filter((book) => {
      if (searchBy === "year") return String(book.year) === normalized;
      if (searchBy === "author") return book.author.toLocaleLowerCase("ru").includes(normalized);
      return book.title.toLocaleLowerCase("ru").includes(normalized);
    });
  }

  AddReader(firstName, lastName = "", phone = "") {
    const readerData = typeof firstName === "object" ? firstName : { firstName, lastName, phone };
    const reader = new Reader(readerData);
    if (this.IsReaderRegistered(reader.name)) return null;
    this.readers.push(reader);
    this.save();
    return reader;
  }

  BorrowBook(title, readerName) {
    const book = this.FindBook(title);
    const reader = this.readers.find((item) => item.name === readerName);
    if (!book || !reader || !this.IsBookAvailable(book.id)) return null;
    const loan = { bookId: book.id, readerId: reader.id, borrowedAt: new Date().toISOString(), dueAt: dateOffset(LOAN_DAYS) };
    this.loans.push(loan);
    this.save();
    return loan;
  }

  ReturnBook(bookId) {
    const loanIndex = this.loans.findIndex((item) => item.bookId === bookId);
    if (loanIndex < 0) return null;
    const [loan] = this.loans.splice(loanIndex, 1);
    this.save();
    const today = new Date();
    const dueDate = new Date(loan.dueAt);
    const todayIndex = Date.UTC(today.getFullYear(), today.getMonth(), today.getDate());
    const dueDateIndex = Date.UTC(dueDate.getFullYear(), dueDate.getMonth(), dueDate.getDate());
    const overdueDays = Math.max(0, Math.floor((todayIndex - dueDateIndex) / 86400000));
    return { loan, fine: Library.CalculateFine(overdueDays), overdueDays };
  }

  CountBooksRecursive(index = 0) {
    if (index >= this.books.length) return 0;
    return 1 + this.CountBooksRecursive(index + 1);
  }

  SortBooksByYear(direction = "asc") {
    const multiplier = direction === "asc" ? 1 : -1;
    return [...this.books].sort((a, b) => (a.year - b.year) * multiplier);
  }

  ShowStatistics() {
    return {
      total: this.CountBooksRecursive(),
      available: this.GetAvailableBooks().length,
      borrowed: this.loans.length,
      readers: this.readers.length,
      comparedTotal: Library.GetMaxBooks(this.books.length, this.loans.length)
    };
  }

  ShowMenu() {
    return ["Добавить книгу", "Показать все книги", "Найти книгу", "Зарегистрировать читателя", "Выдать книгу", "Вернуть книгу", "Показать статистику", "Завершить программу"];
  }

  save() {
    const snapshot = {
      books: this.books,
      readers: this.readers,
      loans: this.loans
    };
    markAdminRequestPending(1);
    this.persistQueue = this.persistQueue.then(async () => {
      try {
        const result = await api("/api/state", {
          method: "PUT",
          body: { ...snapshot, version: this.version }
        });
        this.version = result.version;
        databaseVersion = result.version;
        $("#databaseVersion").textContent = String(result.version);
      } catch (error) {
        console.error("Не удалось сохранить данные библиотеки:", error);
        showToast(error.status === 409
          ? "Данные изменились в другой вкладке. Страница будет обновлена."
          : `Не удалось сохранить изменения: ${error.message}`, true);
        if (error.status === 409) {
          try {
            const latestState = await api("/api/state");
            applyState(latestState);
            render();
          } catch (reloadError) {
            console.error("Не удалось обновить данные библиотеки:", reloadError);
          }
        }
      } finally {
        markAdminRequestPending(-1);
      }
    });
    return this.persistQueue;
  }
}

let sortDirection = "desc";
let showingAllBooks = false;
let showingAllLoans = false;
let showingAllReaders = false;
let pendingBorrowBookId = null;

const bookDialog = $("#bookDialog");
const readerDialog = $("#readerDialog");
const borrowDialog = $("#borrowDialog");
const detailDialog = $("#detailDialog");

function escapeHTML(value) {
  return String(value).replace(/[&<>"']/g, (character) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
  })[character]);
}

function initials(name) {
  return name.trim().split(/\s+/).slice(0, 2).map((part) => part[0] || "").join("").toLocaleUpperCase("ru");
}

function colorFor(value, palette) {
  let hash = 0;
  for (const character of value) hash = (hash * 31 + character.charCodeAt(0)) | 0;
  return palette[Math.abs(hash) % palette.length];
}

function formatDate(value, options = { day: "numeric", month: "short" }) {
  return new Intl.DateTimeFormat("ru-RU", options).format(new Date(value)).replace(" г.", "");
}

function showToast(message, isError = false) {
  const toast = document.createElement("div");
  toast.className = `toast${isError ? " error" : ""}`;
  toast.textContent = message;
  $("#toastRegion").append(toast);
  window.setTimeout(() => toast.remove(), 3400);
}

function readerForLoan(loan) {
  return library.readers.find((reader) => reader.id === loan.readerId);
}

function loanForBook(bookId) {
  return library.loans.find((loan) => loan.bookId === bookId);
}

function renderStats() {
  const stats = library.ShowStatistics();
  $("#totalBooks").textContent = stats.total;
  $("#availableBooks").textContent = stats.available;
  $("#loanedBooks").textContent = stats.borrowed;
  $("#readerCount").textContent = stats.readers;
  $("#navBookCount").textContent = stats.total;
  $("#navLoanCount").textContent = stats.borrowed;
  $("#catalogCount").textContent = stats.total;
  $("#loansCount").textContent = stats.borrowed;
  const availablePercent = stats.total ? Math.round((stats.available / stats.total) * 100) : 0;
  $("#availabilityBar").style.width = `${availablePercent}%`;
  const loanReaders = [...new Set(library.loans.map((loan) => readerForLoan(loan)).filter(Boolean))].slice(0, 4);
  $("#loanAvatars").innerHTML = loanReaders.map((reader) => {
    const background = colorFor(reader.id, AVATAR_COLORS);
    return `<span class="avatar" style="background:${background};color:#68786c">${escapeHTML(initials(reader.name))}</span>`;
  }).join("");
}

function renderBooks() {
  const query = $("#bookSearch").value.trim();
  const filter = $("#bookFilter").value;
  let books = library.ShowBooks();
  if (query) {
    const normalizedQuery = query.toLocaleLowerCase("ru");
    books = library.ShowBooks().filter((book) =>
      ( /^\d{1,4}$/.test(query) && String(book.year) === query)
      || book.title.toLocaleLowerCase("ru").includes(normalizedQuery)
      || book.author.toLocaleLowerCase("ru").includes(normalizedQuery)
      || book.genre.toLocaleLowerCase("ru").includes(normalizedQuery)
    );
  }
  if (filter === "available") books = books.filter((book) => library.IsBookAvailable(book.id));
  if (filter === "borrowed") books = books.filter((book) => !library.IsBookAvailable(book.id));
  if (!query && !showingAllBooks) books = books.slice(0, 5);
  const bookList = $("#bookList");
  $("#emptyBooks").classList.toggle("hidden", books.length > 0);
  bookList.classList.toggle("hidden", books.length === 0);
  bookList.innerHTML = books.map((book) => {
    const loan = loanForBook(book.id);
    const reader = loan && readerForLoan(loan);
    const available = !loan;
    const title = escapeHTML(book.title);
    const author = escapeHTML(book.author);
    const cover = colorFor(book.id, COVER_COLORS);
    const meta = book.genre
      ? `<span class="book-genre-tag">${escapeHTML(book.genre)}</span><span class="meta-dot"></span>${book.year}`
      : `Издание ${book.year}`;
    return `<article class="book-row">
      <button class="book-cover" type="button" data-book-detail="${escapeHTML(book.id)}" style="background:${cover}" aria-label="Подробнее: ${title}">${book.cover ? `<img src="${book.cover}" alt="" />` : escapeHTML(book.title.slice(0, 1))}</button>
      <button class="book-copy plain-book-button" type="button" data-book-detail="${escapeHTML(book.id)}" style="border:0;background:transparent;text-align:left;padding:0">
        <h3 class="book-title">${title}</h3><p class="book-author">${author}${!available ? ` · у читателя ${escapeHTML(reader?.firstName || "")}` : ""}</p><div class="book-meta">${meta}</div>
      </button>
      <div class="book-side"><span class="status-pill ${available ? "status-available" : "status-borrowed"}">${available ? "Доступна" : "На руках"}</span>
        <button class="small-action" type="button" ${available ? `data-borrow="${escapeHTML(book.id)}"` : `data-return="${escapeHTML(book.id)}"`}>${available ? "Выдать" : "Вернуть"}</button></div>
    </article>`;
  }).join("");
}

function renderReaders() {
  const readers = showingAllReaders ? library.readers : library.readers.slice(0, 4);
  const list = $("#readerList");
  if (readers.length === 0) {
    list.innerHTML = '<div class="reader-empty">Зарегистрируйте первого читателя, чтобы оформлять выдачи.</div>';
    return;
  }
  list.innerHTML = readers.map((reader) => {
    const borrowedCount = library.loans.filter((loan) => loan.readerId === reader.id).length;
    const name = escapeHTML(reader.name);
    return `<div class="reader-item"><span class="avatar reader-avatar" style="background:${colorFor(reader.id, AVATAR_COLORS)};color:#68786c">${escapeHTML(initials(reader.name))}</span><span class="reader-info"><strong>${name}</strong><small>${borrowedCount ? `Книг на руках: ${borrowedCount}` : "Читатель библиотеки"}</small></span><i class="reader-active" title="Зарегистрирован"></i></div>`;
  }).join("");
}

function renderLoans() {
  const loans = showingAllLoans ? library.loans : library.loans.slice(0, 4);
  $("#emptyLoans").classList.toggle("hidden", loans.length > 0);
  $("#loanTableBody").innerHTML = loans.map((loan) => {
    const book = library.books.find((item) => item.id === loan.bookId);
    const reader = readerForLoan(loan);
    if (!book || !reader) return "";
    const overdue = Date.now() > new Date(loan.dueAt).getTime();
    const cover = colorFor(book.id, COVER_COLORS);
    const safeId = escapeHTML(book.id);
    return `<tr>
      <td><div class="loan-book"><span class="mini-cover" style="background:${cover}">${escapeHTML(book.title.slice(0, 1))}</span><span class="loan-book-copy"><strong>${escapeHTML(book.title)}</strong><small>${escapeHTML(book.author)}</small></span></div></td>
      <td><span class="table-reader"><span class="avatar" style="background:${colorFor(reader.id, AVATAR_COLORS)};color:#68786c">${escapeHTML(initials(reader.name))}</span>${escapeHTML(reader.name)}</span></td>
      <td>${formatDate(loan.borrowedAt)}</td><td class="${overdue ? "overdue-text" : ""}">${formatDate(loan.dueAt)}</td>
      <td><span class="status-pill ${overdue ? "status-borrowed" : "status-available"}">${overdue ? "Просрочена" : "На руках"}</span></td>
      <td><button class="return-action" type="button" data-return="${safeId}">Вернуть</button></td>
    </tr>`;
  }).join("");
}

function renderAdminPanel() {
  if (!library) return;
  $("#adminBooksTotal").textContent = String(library.books.length);
  $("#adminReadersTotal").textContent = String(library.readers.length);
  $("#adminLoansTotal").textContent = String(library.loans.length);
  $("#databaseVersion").textContent = String(databaseVersion || library.version);
  $("#adminBookRows").innerHTML = library.books.map((book) => `
    <tr>
      <td>${escapeHTML(book.title)}<small class="admin-book-author">${escapeHTML(book.author)}</small></td><td>${escapeHTML(book.genre || "—")}</td><td>${book.year}</td>
      <td><button class="admin-delete-button" type="button" data-edit-book="${escapeHTML(book.id)}">Изменить</button>
      <button class="admin-delete-button" type="button" data-delete-book="${escapeHTML(book.id)}" ${loanForBook(book.id) ? "disabled title=\"Сначала верните книгу\"" : ""}>Удалить</button></td>
    </tr>`).join("");
  $("#adminReaderRows").innerHTML = library.readers.map((reader) => {
    const activeLoans = library.loans.filter((loan) => loan.readerId === reader.id).length;
    return `<tr>
      <td>${escapeHTML(reader.name)}</td><td>${escapeHTML(reader.phone || "—")}</td><td>${activeLoans}</td>
      <td><button class="admin-delete-button" type="button" data-delete-reader="${escapeHTML(reader.id)}" ${activeLoans ? "disabled title=\"Сначала оформите возврат\"" : ""}>Удалить</button></td>
    </tr>`;
  }).join("");
}

function render() {
  if (!library) return;
  renderStats();
  renderBooks();
  renderReaders();
  renderLoans();
  renderAdminPanel();
}

function showBorrowDialog(bookId) {
  const book = library.books.find((item) => item.id === bookId);
  if (!book) return;
  if (library.readers.length === 0) {
    showToast("Сначала зарегистрируйте читателя.", true);
    readerDialog.showModal();
    return;
  }
  pendingBorrowBookId = bookId;
  $("#selectedBook").innerHTML = `${escapeHTML(book.title)}<small>${escapeHTML(book.author)} · ${book.year} год</small>`;
  $("#borrowReaderSelect").innerHTML = library.readers.map((reader) => `<option value="${escapeHTML(reader.id)}">${escapeHTML(reader.name)}</option>`).join("");
  borrowDialog.showModal();
}

function returnBook(bookId) {
  const result = library.ReturnBook(bookId);
  if (!result) {
    showToast("Эта книга уже доступна в библиотеке.", true);
    render();
    return;
  }
  render();
  showToast(result.fine ? `Книга возвращена. Штраф за ${result.overdueDays} дн. просрочки: ${result.fine} ₸.` : "Книга успешно возвращена. Спасибо!");
}

function showBookDetails(bookId) {
  const book = library.books.find((item) => item.id === bookId);
  if (!book) return;
  const loan = loanForBook(book.id);
  const reader = loan && readerForLoan(loan);
  const cover = colorFor(book.id, COVER_COLORS);
  $("#detailTitle").textContent = book.title;
  $("#detailContent").innerHTML = `<div class="detail-info">
    <span class="detail-cover" style="background:${cover}">${book.cover ? `<img src="${book.cover}" alt="Обложка книги «${escapeHTML(book.title)}»" />` : escapeHTML(book.title.slice(0, 1))}</span>
    <div class="detail-facts">
      <div class="detail-fact"><span>Автор</span><strong>${escapeHTML(book.author)}</strong></div>
      <div class="detail-fact"><span>Год издания</span><strong>${book.year}</strong></div>
      <div class="detail-fact"><span>Жанр</span><strong>${escapeHTML(book.genre || "Не указан")}</strong></div>
      <div class="detail-fact"><span>Статус</span><strong>${loan ? `У читателя ${escapeHTML(reader?.name || "")}` : "Доступна для выдачи"}</strong></div>
    </div>
  </div>${book.description ? `<section class="book-description"><h3>О книге</h3><p>${escapeHTML(book.description)}</p></section>` : '<p class="book-description-empty">Описание пока не добавлено.</p>'}`;
  const availabilityAction = loan
    ? `<button class="button button-primary" type="button" data-detail-return="${escapeHTML(book.id)}">Вернуть книгу</button>`
    : `<button class="button button-primary" type="button" data-detail-borrow="${escapeHTML(book.id)}">Выдать читателю</button>`;
  $("#detailActions").innerHTML = `<button class="button button-quiet" type="button" data-edit-book="${escapeHTML(book.id)}">Изменить</button>${availabilityAction}<button class="button button-quiet" type="button" data-close-dialog>Закрыть</button>`;
  detailDialog.showModal();
}

function startBookEdit(bookId) {
  const book = library.books.find((item) => item.id === bookId);
  if (!book) return;
  detailDialog.close();
  $("#bookForm").elements.id.value = book.id;
  $("#bookForm").elements.title.value = book.title;
  $("#bookForm").elements.author.value = book.author;
  $("#bookForm").elements.year.value = String(book.year);
  $("#bookForm").elements.genre.value = book.genre;
  $("#bookForm").elements.description.value = book.description;
  $("#bookForm").elements.coverFile.value = "";
  setBookCoverPreview(book.cover);
  $("#bookDialogTitle").textContent = "Изменить книгу";
  bookDialog.showModal();
}

function deleteBook(bookId) {
  const book = library.books.find((item) => item.id === bookId);
  if (!book || loanForBook(bookId)) {
    showToast("Сначала верните книгу, затем её можно удалить.", true);
    return;
  }
  if (!window.confirm(`Удалить книгу «${book.title}» из каталога?`)) return;
  library.books = library.books.filter((item) => item.id !== bookId);
  Library.TotalBooks = library.books.length;
  library.save();
  render();
  showToast("Книга удалена из каталога.");
}

function deleteReader(readerId) {
  const reader = library.readers.find((item) => item.id === readerId);
  if (!reader) return;
  if (library.loans.some((loan) => loan.readerId === readerId)) {
    showToast("Сначала оформите возврат всех книг этого читателя.", true);
    return;
  }
  if (!window.confirm(`Удалить читателя «${reader.name}»?`)) return;
  library.readers = library.readers.filter((item) => item.id !== readerId);
  library.save();
  render();
  showToast("Читатель удалён.");
}

let stagedBookCover = "";

function setBookCoverPreview(cover) {
  stagedBookCover = cover;
  $("#coverPreviewBox").classList.toggle("hidden", !cover);
  if (cover) $("#coverPreview").src = cover;
  else $("#coverPreview").removeAttribute("src");
}

async function readBookCover(file) {
  if (!file.type.match(/^image\/(png|jpeg|webp)$/)) {
    throw new Error("Выберите изображение в формате PNG, JPG или WebP.");
  }
  if (file.size > 8_000_000) {
    throw new Error("Исходное изображение больше 8 МБ.");
  }
  const imageBitmap = await createImageBitmap(file);
  try {
    const scale = Math.min(1, 900 / Math.max(imageBitmap.width, imageBitmap.height));
    const canvas = document.createElement("canvas");
    canvas.width = Math.max(1, Math.round(imageBitmap.width * scale));
    canvas.height = Math.max(1, Math.round(imageBitmap.height * scale));
    const context = canvas.getContext("2d");
    if (!context) throw new Error("Не удалось подготовить изображение.");
    context.drawImage(imageBitmap, 0, 0, canvas.width, canvas.height);
    let quality = 0.86;
    let result = canvas.toDataURL("image/webp", quality);
    if (!result.startsWith("data:image/webp,")) {
      result = canvas.toDataURL("image/jpeg", quality);
    }
    while (result.length > 1_850_000 && quality > 0.4) {
      quality -= 0.1;
      const imageType = result.startsWith("data:image/webp,") ? "image/webp" : "image/jpeg";
      result = canvas.toDataURL(imageType, quality);
    }
    if (!/^data:image\/(webp|jpeg);/.test(result) || result.length > 1_850_000) {
      throw new Error("Не удалось уменьшить изображение до допустимого размера.");
    }
    return result;
  } finally {
    imageBitmap.close();
  }
}

$("#bookForm").elements.coverFile.addEventListener("change", async (event) => {
  const fileInput = event.currentTarget;
  const [file] = fileInput.files;
  if (!file) return;
  try {
    setBookCoverPreview(await readBookCover(file));
  } catch (error) {
    fileInput.value = "";
    showToast(error.message || "Не удалось загрузить обложку.", true);
  }
});

$("#removeCoverButton").addEventListener("click", () => {
  $("#bookForm").elements.coverFile.value = "";
  setBookCoverPreview("");
});

$("#bookDialog").addEventListener("close", () => {
  $("#bookForm").reset();
  $("#bookDialogTitle").textContent = "Новая книга";
  setBookCoverPreview("");
});

$("#bookForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const formData = new FormData(event.currentTarget);
  const id = String(formData.get("id") || "");
  const title = String(formData.get("title")).trim();
  const author = String(formData.get("author")).trim();
  const year = Number(formData.get("year"));
  const genre = String(formData.get("genre") || "").trim();
  const description = String(formData.get("description") || "").trim();
  if (!title || !author || !Number.isInteger(year) || year < 1 || year > 2100) {
    showToast("Проверьте название, автора и год издания.", true);
    return;
  }
  if (library.books.some((book) => book.title.toLocaleLowerCase("ru") === title.toLocaleLowerCase("ru") && book.id !== id)) {
    showToast("Книга с таким названием уже есть в каталоге.", true);
    return;
  }
  const submitButton = event.currentTarget.querySelector('button[type="submit"]');
  submitButton.disabled = true;
  if (id) {
    const book = library.books.find((item) => item.id === id);
    if (!book) {
      showToast("Книга больше не найдена в каталоге.", true);
      return;
    }
    book.SetTitle(title);
    book.author = author;
    book.year = year;
    book.genre = genre;
    book.description = description;
    book.cover = stagedBookCover;
    library.save();
    showToast(`Данные книги «${title}» обновлены.`);
  } else {
    library.AddBook({ title, author, year, genre, description, cover: stagedBookCover });
    showToast(`«${title}» добавлена в каталог.`);
  }
  event.currentTarget.reset();
  $("#bookDialogTitle").textContent = "Новая книга";
  bookDialog.close();
  render();
  await library.persistQueue;
  submitButton.disabled = false;
});

$("#readerForm").addEventListener("submit", (event) => {
  event.preventDefault();
  const formData = new FormData(event.currentTarget);
  const reader = library.AddReader({
    firstName: String(formData.get("firstName")).trim(),
    lastName: String(formData.get("lastName")).trim(),
    phone: String(formData.get("phone")).trim()
  });
  if (!reader) {
    showToast("Читатель с таким именем уже зарегистрирован.", true);
    return;
  }
  event.currentTarget.reset();
  readerDialog.close();
  render();
  showToast(`${reader.name} добавлен(а) в список читателей.`);
});

$("#borrowForm").addEventListener("submit", (event) => {
  event.preventDefault();
  const readerId = $("#borrowReaderSelect").value;
  const reader = library.readers.find((item) => item.id === readerId);
  const book = library.books.find((item) => item.id === pendingBorrowBookId);
  if (!reader || !book || !library.IsBookAvailable(book.id)) {
    showToast("Не удалось оформить выдачу. Обновите список и попробуйте ещё раз.", true);
    borrowDialog.close();
    render();
    return;
  }
  const loan = library.BorrowBook(book.title, reader.name);
  if (!loan) {
    showToast("Не удалось оформить выдачу. Проверьте читателя и статус книги.", true);
    return;
  }
  borrowDialog.close();
  render();
  showToast(`«${book.title}» выдана читателю ${reader.firstName}.`);
});

$("#bookSearch").addEventListener("input", renderBooks);
$("#bookFilter").addEventListener("change", renderBooks);
$("#sortBooks").addEventListener("click", () => {
  sortDirection = sortDirection === "asc" ? "desc" : "asc";
  const sorted = library.SortBooksByYear(sortDirection);
  library.books = sorted;
  $("#sortBooks").innerHTML = `Год <span>${sortDirection === "asc" ? "↑" : "↓"}</span>`;
  showingAllBooks = true;
  renderBooks();
});

$("#showAllBooks").addEventListener("click", () => {
  showingAllBooks = !showingAllBooks;
  $("#showAllBooks").innerHTML = showingAllBooks ? "Свернуть <span>↑</span>" : "Смотреть все <span>→</span>";
  renderBooks();
});

$("#showLoans").addEventListener("click", () => {
  showingAllLoans = !showingAllLoans;
  $("#showLoans").innerHTML = showingAllLoans ? "Последние выдачи <span>↑</span>" : "Все выдачи <span>→</span>";
  renderLoans();
});

$("#showAllReaders").addEventListener("click", () => {
  showingAllReaders = !showingAllReaders;
  $("#showAllReaders").innerHTML = showingAllReaders ? "Свернуть <span>↑</span>" : "Все <span>→</span>";
  renderReaders();
});

document.addEventListener("click", (event) => {
  const target = event.target instanceof Element ? event.target : null;
  if (!target) return;
  const adminButton = target.closest("[data-admin-panel]");
  if (adminButton) {
    renderAdminPanel();
    $("#adminDialog").showModal();
    return;
  }
  const editBookButton = target.closest("[data-edit-book]");
  if (editBookButton) {
    startBookEdit(editBookButton.dataset.editBook);
    return;
  }
  const deleteBookButton = target.closest("[data-delete-book]");
  if (deleteBookButton) {
    deleteBook(deleteBookButton.dataset.deleteBook);
    return;
  }
  const deleteReaderButton = target.closest("[data-delete-reader]");
  if (deleteReaderButton) {
    deleteReader(deleteReaderButton.dataset.deleteReader);
    return;
  }
  const openButton = target.closest("[data-open-modal]");
  if (openButton) {
    const dialog = openButton.dataset.openModal === "book" ? bookDialog : readerDialog;
    dialog.showModal();
    return;
  }
  if (target.closest("[data-close-dialog]")) {
    target.closest("dialog")?.close();
    return;
  }
  const detailButton = target.closest("[data-book-detail]");
  if (detailButton) {
    showBookDetails(detailButton.dataset.bookDetail);
    return;
  }
  const borrowButton = target.closest("[data-borrow]");
  if (borrowButton) {
    showBorrowDialog(borrowButton.dataset.borrow);
    return;
  }
  const returnButton = target.closest("[data-return]");
  if (returnButton) {
    returnBook(returnButton.dataset.return);
    return;
  }
  const detailBorrowButton = target.closest("[data-detail-borrow]");
  if (detailBorrowButton) {
    const { detailBorrow } = detailBorrowButton.dataset;
    detailDialog.close();
    showBorrowDialog(detailBorrow);
    return;
  }
  const detailReturnButton = target.closest("[data-detail-return]");
  if (detailReturnButton) {
    detailDialog.close();
    returnBook(detailReturnButton.dataset.detailReturn);
  }
});

$$(".nav-link").forEach((link) => {
  link.addEventListener("click", () => {
    $$(".nav-link").forEach((item) => item.classList.remove("active"));
    link.classList.add("active");
  });
});

document.addEventListener("keydown", (event) => {
  if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
    event.preventDefault();
    $("#bookSearch").focus();
    document.querySelector("#catalog").scrollIntoView({ behavior: "smooth", block: "start" });
  }
});

$("#mobileMenuButton").addEventListener("click", () => {
  const catalog = $("#catalog");
  catalog.scrollIntoView({ behavior: "smooth", block: "start" });
  $("#bookSearch").focus({ preventScroll: true });
});

function showAuthError(message) {
  const error = $("#authError");
  error.textContent = message;
  error.classList.remove("hidden");
}

function setAuthMode(setupRequired) {
  $("#loginForm").classList.toggle("hidden", setupRequired);
  $("#setupForm").classList.toggle("hidden", !setupRequired);
  $("#authTitle").textContent = setupRequired ? "Создание администратора" : "Вход администратора";
  $("#authDescription").textContent = setupRequired
    ? "Создайте учётную запись — только вы сможете управлять библиотекой."
    : "Войдите, чтобы управлять каталогом и читателями.";
}

async function api(url, options = {}) {
  const headers = new Headers(options.headers || {});
  const request = { ...options, headers, credentials: "same-origin" };
  if (options.body && typeof options.body !== "string") {
    headers.set("Content-Type", "application/json");
    request.body = JSON.stringify(options.body);
  }
  let response;
  try {
    response = await fetch(url, request);
  } catch (error) {
    throw new Error("Не удалось подключиться к серверу. Проверьте, что сервер запущен.");
  }
  const contentType = response.headers.get("content-type") || "";
  const result = contentType.includes("application/json") ? await response.json() : null;
  if (!response.ok) {
    const error = new Error(result?.error || `Ошибка сервера (${response.status}).`);
    error.status = response.status;
    throw error;
  }
  return result;
}

function applyState(state) {
  library = new Library(state);
  databaseVersion = state.version;
  currentSettings = state.settings;
  LOAN_DAYS = state.settings.loan_days;
  FINE_PER_DAY = state.settings.fine_per_day;
  $("#settingsForm").elements.loanDays.value = String(LOAN_DAYS);
  $("#settingsForm").elements.finePerDay.value = String(FINE_PER_DAY);
  $("#adminUsername").textContent = adminUsername;
  $("#panelUsername").textContent = adminUsername;
  $("#welcomeUsername").textContent = adminUsername;
  $("#borrowDialog .field-hint").textContent =
    `Срок выдачи — ${LOAN_DAYS} дн. Если срок будет превышен, штраф составит ${FINE_PER_DAY} ₸ за каждый день.`;
}

async function enterLibrary(username) {
  adminUsername = username;
  const state = await api("/api/state");
  applyState(state);
  $("#authScreen").classList.add("hidden");
  $(".app-shell").classList.remove("auth-locked");
  render();
}

$("#loginForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  $("#authError").classList.add("hidden");
  const formData = new FormData(event.currentTarget);
  const submitButton = event.currentTarget.querySelector('button[type="submit"]');
  submitButton.disabled = true;
  try {
    const result = await api("/api/login", {
      method: "POST",
      body: { username: formData.get("username"), password: formData.get("password") }
    });
    await enterLibrary(result.username);
  } catch (error) {
    showAuthError(error.message);
  } finally {
    submitButton.disabled = false;
  }
});

$("#setupForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  $("#authError").classList.add("hidden");
  const formData = new FormData(event.currentTarget);
  const password = String(formData.get("password"));
  if (password !== formData.get("confirmPassword")) {
    showAuthError("Пароли не совпадают.");
    return;
  }
  const submitButton = event.currentTarget.querySelector('button[type="submit"]');
  submitButton.disabled = true;
  try {
    const result = await api("/api/setup", {
      method: "POST",
      body: { username: formData.get("username"), password }
    });
    await enterLibrary(result.username);
    showToast("Учётная запись администратора создана.");
  } catch (error) {
    showAuthError(error.message);
  } finally {
    submitButton.disabled = false;
  }
});

$("#settingsForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const formData = new FormData(event.currentTarget);
  const submitButton = event.currentTarget.querySelector('button[type="submit"]');
  submitButton.disabled = true;
  markAdminRequestPending(1);
  const settings = {
    loan_days: Number(formData.get("loanDays")),
    fine_per_day: Number(formData.get("finePerDay"))
  };
  try {
    currentSettings = await api("/api/admin/settings", { method: "PUT", body: settings });
    LOAN_DAYS = currentSettings.loan_days;
    FINE_PER_DAY = currentSettings.fine_per_day;
    $("#borrowDialog .field-hint").textContent =
      `Срок выдачи — ${LOAN_DAYS} дн. Если срок будет превышен, штраф составит ${FINE_PER_DAY} ₸ за каждый день.`;
    showToast("Правила выдачи сохранены.");
  } catch (error) {
    showToast(`Не удалось сохранить настройки: ${error.message}`, true);
  } finally {
    submitButton.disabled = false;
    markAdminRequestPending(-1);
  }
});

$("#passwordForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const formData = new FormData(event.currentTarget);
  const submitButton = event.currentTarget.querySelector('button[type="submit"]');
  submitButton.disabled = true;
  markAdminRequestPending(1);
  try {
    await api("/api/admin/password", {
      method: "POST",
      body: {
        currentPassword: formData.get("currentPassword"),
        newPassword: formData.get("newPassword")
      }
    });
    event.currentTarget.reset();
    showToast("Пароль администратора изменён.");
  } catch (error) {
    showToast(`Не удалось изменить пароль: ${error.message}`, true);
  } finally {
    submitButton.disabled = false;
    markAdminRequestPending(-1);
  }
});

$("#logoutButton").addEventListener("click", async () => {
  const button = $("#logoutButton");
  button.disabled = true;
  try {
    if (library) await library.persistQueue;
    await api("/api/logout", { method: "POST", body: {} });
    $("#adminDialog").close();
    library = null;
    $(".app-shell").classList.add("auth-locked");
    $("#authScreen").classList.remove("hidden");
    $("#loginForm").reset();
    $("#setupForm").reset();
    $("#authError").classList.add("hidden");
    setAuthMode(false);
  } catch (error) {
    showToast(`Не удалось завершить сеанс: ${error.message}`, true);
  } finally {
    button.disabled = pendingAdminRequests > 0;
  }
});

async function boot() {
  $(".app-shell").classList.add("auth-locked");
  try {
    const status = await api("/api/bootstrap");
    setAuthMode(status.setupRequired);
    if (status.authenticated) {
      await enterLibrary(status.username);
    } else {
      $("#authScreen").classList.remove("hidden");
    }
  } catch (error) {
    setAuthMode(false);
    $("#authScreen").classList.remove("hidden");
    showAuthError(error.message);
  }
}

$("#todayLabel").textContent = new Intl.DateTimeFormat("ru-RU", { day: "numeric", month: "long", year: "numeric" }).format(new Date());
$("#yearNow").textContent = new Date().getFullYear();
boot();
